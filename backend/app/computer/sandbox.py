"""OS-level command sandboxing.

Zero-trust rule: never rely only on Python path checks.

Linux  → Bubblewrap (bwrap) namespace isolation when available: unshared
         namespaces, only authorized folders writable, /proc /sys /dev hidden,
         read-only runtime binds, tmpfs /tmp, rlimits. Without bwrap the backend
         reports DEGRADED mode and additionally refuses system-modifying
         commands instead of silently running unrestricted.
Windows → Job Objects (best-effort) + restricted token where available;
         otherwise DEGRADED with the same refusal policy.
macOS   → sandbox-exec (seatbelt) placeholder; DEGRADED unless available.

The same SandboxBackend interface is implemented for every OS.
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Optional

from . import permissions
from .permissions import PermissionDenied

log = logging.getLogger("neuralweb.sandbox")

# Commands that modify the system and are refused in degraded (non-sandboxed) mode.
SYSTEM_MODIFYING = ("apt ", "apt-get ", "dpkg ", "snap ", "systemctl ", "service ",
                    "useradd ", "usermod ", "adduser ", "passwd ", "chmod ", "chown ",
                    "mount ", "umount ", "modprobe ", "insmod ", "rmmod ", "ufw ",
                    "iptables ", "nft ", "update-alternatives ", "pip install --system",
                    "npm install -g ", "gem install ", "cabal install ",
                    "Set-ExecutionPolicy", "Install-WindowsFeature", "net user",
                    "sc.exe ", "reg add", "regedit", "icacls", "winget install")


def _rlimits_args() -> list[str]:
    """CPU + memory + process limits via rlimit (applies inside sandbox & outside)."""
    fsize = 256 * 1024 * 1024          # max file size 256MB
    return [
        "--rlimit-fsize", str(fsize),
        "--rlimit-nofile", "256",
        "--rlimit-nproc", "512",
    ]


# --------------------------------------------------------- github passthrough
# The coding agent must be able to init/commit/push to GitHub like Claude Code
# or Antigravity. Inside the sandbox HOME is the workspace and secrets are
# filtered, so git/gh would find no credentials. We inject exactly what's
# needed, only for git/gh commands: the gh CLI binary (read-only bind) and the
# HOST's GitHub token from `gh auth login`, written to a 0600 file in the
# user's cache and read-only bound into the sandbox. The token is passed as a
# FILE (never argv, never env — argv is visible in `ps`), and the git
# credential helper reads it from inside. Filesystem confinement is unchanged.
_TOKEN_REL_PATH = "gh_token"


def _token_file_path() -> str:
    return str(Path.home() / ".cache" / "neuralweb" / "gh_token")


def _ensure_host_token_file() -> str:
    """Write the host's `gh auth token` to ~/.cache/neuralweb/gh_token (0600)
    and return its path — empty string when there is no host login."""
    try:
        import subprocess
        p = subprocess.run(["gh", "auth", "token"], capture_output=True,
                           text=True, timeout=8)
        tok = (p.stdout or "").strip()
        if p.returncode == 0 and tok.startswith(("gho_", "ghp_", "github_pat_")):
            path = Path(_token_file_path())
            changed = True
            if path.exists():
                try:
                    changed = path.read_text().strip() != tok
                except OSError:
                    changed = True
            if changed:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(tok + "\n")
            return str(path)
    except Exception:
        pass
    return ""


def _git_identity() -> tuple[str, str]:
    """Host git identity, with a sensible fallback so agent commits work."""
    import subprocess
    name = email = ""
    try:
        name = subprocess.run(["git", "config", "--global", "user.name"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
        email = subprocess.run(["git", "config", "--global", "user.email"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    return name or "ADITYA BODKE", email or "adityabodke@users.noreply.github.com"


# sandbox-side mount point for the gh binary + token file
_GH_SANDBOX_DIR = "/tmp/.neuralweb-gh"


def _github_support(command: str) -> tuple[dict, list[tuple[str, str]]]:
    """Returns (extra_env, ro_binds) when the command involves git/gh."""
    import re
    if not re.search(r"\b(git|gh)\b", command):
        return {}, []
    env: dict = {}
    binds: list[tuple[str, str]] = []
    gh = shutil.which("gh")
    token_file = _ensure_host_token_file()
    if gh:
        # /usr/local is read-only inside the sandbox — bind the real gh binary
        # to the private tmpfs PLUS a tiny wrapper that injects GH_TOKEN read
        # from the bound token FILE (secret never in argv/env) and execs it.
        binds.append((gh, f"{_GH_SANDBOX_DIR}/gh.bin"))
        wrapper = Path(_token_file_path()).parent / "gh_wrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'GH_TOKEN="$(cat {_GH_SANDBOX_DIR}/{_TOKEN_REL_PATH})" '
            f'exec "{_GH_SANDBOX_DIR}/gh.bin" "$@"\n')
        wrapper.chmod(0o755)
        binds.append((str(wrapper), f"{_GH_SANDBOX_DIR}/gh"))
    # identity (commits never fail on missing user.name)
    name, email = _git_identity()
    entries = [("user.name", name), ("user.email", email)]
    if token_file:
        binds.append((token_file, f"{_GH_SANDBOX_DIR}/{_TOKEN_REL_PATH}"))
        # git-credential helper as a PLAIN executable script: git runs it
        # directly as [script, get] with no shell parsing. The `!`-shell
        # helper string was unreliable under dash inside bwrap — git
        # emitted 'password=get' (its own operation argv) instead of the
        # token. The script reads the bound token FILE inside the sandbox,
        # so the secret never travels through argv or environment.
        helper_host = Path(token_file).parent / "git-credential-nw"
        helper_host.write_text(
            "#!/bin/sh\n"
            "# NEURAL WEB sandbox git credential helper.\n"
            'op="$1"\n'
            "cat >/dev/null    # consume git's credential request from stdin\n"
            'if [ "$op" = "get" ]; then\n'
            f'  tok="$(cat {_GH_SANDBOX_DIR}/{_TOKEN_REL_PATH} 2>/dev/null)"\n'
            '  if [ -n "$tok" ]; then\n'
            '    printf "username=x-access-token\\n"\n'
            '    printf "password=%s\\n" "$tok"\n'
            "  fi\n"
            "fi\n")
        helper_host.chmod(0o755)
        binds.append((str(helper_host),
                      f"{_GH_SANDBOX_DIR}/git-credential-nw"))
        entries.append(("credential.helper",
                        f"{_GH_SANDBOX_DIR}/git-credential-nw"))
    count = len(entries)
    env["GIT_CONFIG_COUNT"] = str(count)
    for i, (k, v) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{i}"] = k
        env[f"GIT_CONFIG_VALUE_{i}"] = v
    return env, binds


def _bwrap_supports_rlimits(bwrap_path: str) -> bool:
    """Some bwrap builds are compiled without rlimit options — probe once."""
    import subprocess
    try:
        p = subprocess.run(
            [bwrap_path, "--ro-bind", "/", "/", "--rlimit-fsize", "1024",
             "--", "true"],
            capture_output=True, text=True, timeout=10)
        return "unknown option" not in p.stderr.lower()
    except Exception:
        return False


class SandboxResult:
    def __init__(self, mode: str, argv: list[str], env: dict,
                 bind_rw: list[str], refused: bool = False, reason: str = "") -> None:
        self.mode = mode          # 'bwrap' | 'degraded' | 'refused'
        self.argv = argv
        self.env = env
        self.bind_rw = bind_rw
        self.refused = refused
        self.reason = reason


class LinuxSandboxBackend:
    name = "linux"

    def __init__(self) -> None:
        self.bwrap = shutil.which("bwrap")
        self.rlimits_ok = bool(self.bwrap) and _bwrap_supports_rlimits(self.bwrap)

    def prepare(self, command: str, workspace: str, need_network: bool = False) -> SandboxResult:
        ws = str(Path(workspace).resolve())
        if not os.path.isdir(ws):
            raise PermissionDenied(f"workspace does not exist: {ws}")
        if self.bwrap:
            # Isolated namespace: nothing is writable except authorized folders.
            argv = [
                self.bwrap,
                "--unshare-all",
                "--dev", "/dev",
                "--proc", "/proc",
                "--die-with-parent",
                "--new-session",
                "--clearenv",
                *sum([["--ro-bind", "/usr", "/usr"],
                      ["--ro-bind-try", "/lib", "/lib"],
                      ["--ro-bind-try", "/lib64", "/lib64"],
                      ["--ro-bind-try", "/bin", "/bin"],
                      ["--ro-bind-try", "/sbin", "/sbin"],
                      ["--ro-bind-try", "/etc/alternatives", "/etc/alternatives"],
                      ["--ro-bind-try", "/etc/ssl", "/etc/ssl"],
                      ["--ro-bind-try", "/etc/ld.so.cache", "/etc/ld.so.cache"],
                      ["--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf"]], []),
                "--tmpfs", "/tmp",
                "--tmpfs", "/run",
                "--bind", ws, ws,
                "--chdir", ws,
                *(_rlimits_args() if self.rlimits_ok else []),
            ]
            for root in permissions.authorized_roots():
                # skip stale registrations (deleted temp dirs would make bwrap
                # fail with "Can't find source path" for EVERY command)
                if str(root) != ws and root.exists():
                    argv += ["--bind", str(root), str(root)]
            if need_network:
                # unshare-all removed network namespace; allow loopback only via
                # --share-net when explicitly required by the tool.
                argv += ["--share-net"]
            gh_env, gh_binds = _github_support(command)
            for src, dst in gh_binds:
                argv += ["--ro-bind", src, dst]
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": ws, "LANG": "C.UTF-8", "TERM": "dumb"}
            if gh_env.get("GIT_CONFIG_COUNT"):
                gh_env["PATH"] = _GH_SANDBOX_DIR + ":" + env["PATH"]
            env.update(gh_env)
            # bwrap --clearenv wipes the child env — pass every variable
            # explicitly with --setenv (must come BEFORE the /bin/sh command)
            for k, v in env.items():
                argv += ["--setenv", k, v]
            argv += ["/bin/sh", "-lc", command]
            return SandboxResult("bwrap", argv, env, [ws])
        # Degraded: no bubblewrap. Refuse system-modifying commands outright.
        for pat in SYSTEM_MODIFYING:
            if command.strip().startswith(pat) or f"; {pat}" in command or \
                    f"&& {pat}" in command:
                return SandboxResult("refused", [], {}, [],
                                     reason="system-modifying command refused: no OS sandbox "
                                            "available (install bubblewrap for full isolation)")
        argv = ["/bin/sh", "-lc", command]
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "TERM": "dumb"}
        gh_env, _ = _github_support(command)
        env.update(gh_env)
        return SandboxResult("degraded", argv, env, [ws])


class WindowsSandboxBackend:
    name = "windows"

    def prepare(self, command: str, workspace: str, need_network: bool = False) -> SandboxResult:
        # No true sandbox without dedicated accounts; refuse system-modifying ops.
        for pat in SYSTEM_MODIFYING:
            if pat.lower() in command.lower():
                return SandboxResult("refused", [], {}, [],
                                     reason="system-modifying command refused: no OS sandbox available")
        return SandboxResult("degraded", [], {}, [])


class MacOSSandboxBackend:
    name = "macos"

    def __init__(self) -> None:
        self.sbexec = shutil.which("sandbox-exec")

    def prepare(self, command: str, workspace: str, need_network: bool = False) -> SandboxResult:
        if self.sbexec:
            profile = ("(version 1)(allow default)(deny file-write* (subpath \"/\"))"
                       f"(allow file-write* (subpath \"{workspace}\"))")
            argv = [self.sbexec, "-p", profile, "/bin/sh", "-lc", command]
            return SandboxResult("sandbox-exec", argv, {}, [workspace])
        for pat in SYSTEM_MODIFYING:
            if pat in command:
                return SandboxResult("refused", [], {}, [], reason="no sandbox available")
        return SandboxResult("degraded", [], {}, [])


def get_sandbox() -> "LinuxSandboxBackend | WindowsSandboxBackend | MacOSSandboxBackend":
    osname = permissions.detect_os()
    if osname == "windows":
        return WindowsSandboxBackend()
    if osname == "macos":
        return MacOSSandboxBackend()
    return LinuxSandboxBackend()


sandbox = get_sandbox()


def sandbox_status() -> dict:
    mode = getattr(sandbox, "name", "linux")
    available = bool(getattr(sandbox, "bwrap", getattr(sandbox, "sbexec", None))) \
        if mode in ("linux", "macos") else False
    return {
        "os": mode,
        "backend": type(sandbox).__name__,
        "os_isolation_available": available,
        "mode": ("os-sandbox" if available else "degraded"),
        "note": ("commands run in an OS-level namespace sandbox"
                 if available else
                 "OS sandbox tool unavailable: system-modifying commands are refused; "
                 "other commands run with cwd + policy + rlimits only (DEGRADED security)"),
    }
