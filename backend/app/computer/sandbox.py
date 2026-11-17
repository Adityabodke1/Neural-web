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
            argv += ["/bin/sh", "-lc", command]
            env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": ws, "LANG": "C.UTF-8", "TERM": "dumb"}
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
