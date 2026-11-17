"""OS-specific execution backends. The agent detects the OS once and uses the
matching backend for shell commands and system information."""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
from typing import Optional

from ..computer.permissions import detect_os


class ExecutionBackend:
    os_name: str = "unknown"

    async def run(self, command: str, cwd: Optional[str] = None,
                  timeout: float = 120.0, env_extra: dict | None = None) -> dict:
        raise NotImplementedError

    def default_shell(self) -> str:
        raise NotImplementedError

    def system_info(self) -> dict:
        raise NotImplementedError


def _clean_env(env_extra: dict | None = None) -> dict:
    """Never leak API keys into child processes or their output."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    env["TERM"] = "dumb"
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    if env_extra:
        env.update(env_extra)
    return env


def _mask_secrets(text: str) -> str:
    import re
    patterns = [r"gsk_[A-Za-z0-9]{10,}", r"sk-[A-Za-z0-9_-]{10,}", r"AIza[A-Za-z0-9_-]{10,}",
                r"Bearer\s+[A-Za-z0-9_\-\.]{10,}", r"[A-Za-z0-9_\-]{20,}\.us\.east-1"]
    for p in patterns:
        text = re.sub(p, "[REDACTED]", text)
    return text


class LinuxBackend(ExecutionBackend):
    os_name = "linux"

    def default_shell(self) -> str:
        return os.environ.get("SHELL", "/bin/bash") if os.path.exists(
            os.environ.get("SHELL", "/bin/bash")) else "/bin/bash"

    async def run(self, command: str, cwd: Optional[str] = None,
                  timeout: float = 120.0, env_extra: dict | None = None) -> dict:
        shell = self.default_shell()
        proc = await asyncio.create_subprocess_exec(
            shell, "-lc", command,
            cwd=cwd or os.path.expanduser("~"),
            env=_clean_env(env_extra),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            preexec_fn=os.setsid if detect_os() == "linux" else None)
        out = b""
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                import signal
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        text = out.decode("utf-8", "replace")[-100_000:]
        return {"exit_code": proc.returncode, "stdout": _mask_secrets(text),
                "timed_out": timed_out,
                "shell": shell, "os": self.os_name}

    def system_info(self) -> dict:
        info = {
            "os": self.os_name,
            "distro": platform.freedesktop_os_release().get("PRETTY_NAME", "linux")
            if hasattr(platform, "freedesktop_os_release") else "linux",
            "kernel": platform.release(),
            "arch": platform.machine(),
            "python": platform.python_version(),
            "hostname": platform.node(),
        }
        for tool in ["git", "node", "npm", "python3", "pip3", "gcc", "g++", "java",
                     "cargo", "go", "docker", "pnpm", "uv"]:
            path = shutil.which(tool)
            if path:
                info[f"has_{tool}"] = path
        return info


class WindowsBackend(ExecutionBackend):
    os_name = "windows"

    def default_shell(self) -> str:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        return ps or "cmd.exe"

    async def run(self, command: str, cwd: Optional[str] = None,
                  timeout: float = 120.0, env_extra: dict | None = None) -> dict:
        shell = self.default_shell()
        if shell.endswith(("powershell.exe", "pwsh.exe", "pwsh")):
            argv = [shell, "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = [shell, "/C", command]
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=_clean_env(env_extra),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            creationflags=getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0))
        out = b""
        timed_out = False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except Exception:
                pass
        return {"exit_code": proc.returncode, "stdout": _mask_secrets(
            out.decode("utf-8", "replace")[-100_000:]), "timed_out": timed_out,
            "shell": shell, "os": self.os_name}

    def system_info(self) -> dict:
        return {"os": self.os_name, "release": platform.release(),
                "version": platform.version(), "arch": platform.machine(),
                "python": platform.python_version(), "hostname": platform.node()}


class MacOSBackend(LinuxBackend):
    os_name = "macos"

    def system_info(self) -> dict:
        return {"os": self.os_name, "release": platform.release(),
                "arch": platform.machine(), "python": platform.python_version()}


def get_backend() -> ExecutionBackend:
    name = detect_os()
    return {"linux": LinuxBackend, "windows": WindowsBackend,
            "macos": MacOSBackend}.get(name, LinuxBackend)()


execution_backend = get_backend()
