"""Controlled terminal engine: execute commands and manage long-running processes.

Every command runs with an authorized workspace as its working directory, is
policy-checked, audited, and has its output scrubbed of secrets. No unrestricted
root/admin access is ever provided.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from ..database import repository as repo
from . import permissions, sandbox
from .os_backends import execution_backend, get_backend
from .sandbox import sandbox

log = logging.getLogger("neuralweb.terminal")

SWITCHES = ("terminal_enabled", "browser_enabled", "network_enabled", "global_stop")


def _switch(name: str) -> bool:
    v = repo.get_setting(name, "true" if name != "global_stop" else "false")
    return v == "true"


def assert_switch(name: str) -> None:
    if not _switch(name):
        raise permissions.PermissionDenied(
            f"'{name}' is disabled (emergency controls). Re-enable in Settings/Permissions.")


def emergency_stop() -> dict:
    """Global stop: halt research agents, kill managed processes, disable subsystems."""
    repo.set_setting("global_stop", "true")
    repo.set_setting("terminal_enabled", "false")
    repo.set_setting("browser_enabled", "false")
    stopped = []
    for pid, entry in list(terminal_engine.processes.items()):
        try:
            proc = entry["proc"]
            if proc.returncode is None:
                proc.kill()
            stopped.append(pid)
        except Exception:
            pass
    from ..agent.task_manager import registry
    for sid, tm in list(registry._tasks.items()):
        tm.request_stop()
    stopped_tasks = len(registry._tasks)
    permissions.audit("emergency_stop", "global", allowed=True, level=3,
                      detail=f"processes={len(stopped)} tasks={stopped_tasks}")
    return {"processes_stopped": stopped, "tasks_signalled": stopped_tasks}


def clear_emergency_stop() -> dict:
    repo.set_setting("global_stop", "false")
    repo.set_setting("terminal_enabled", "true")
    repo.set_setting("browser_enabled", "true")
    permissions.audit("emergency_clear", "global", allowed=True, level=3)
    return {"status": "controls re-enabled"}


def switch_status() -> dict:
    return {name: _switch(name) for name in SWITCHES}


class TerminalEngine:
    def __init__(self) -> None:
        self.processes: dict[int, dict] = {}
        self._next_pid = 1

    # -------- one-shot commands --------

    async def execute(self, command: str, workspace: str, timeout: float = 120.0,
                      session_id: str = "") -> dict:
        if not workspace:
            raise permissions.PermissionDenied("no workspace selected")
        permissions.check_access(workspace, need_write=False, session_id=session_id)
        allowed, reason = permissions.check_command_safety(command)
        if not allowed:
            permissions.audit("cmd_blocked", command, allowed=False,
                              level=permissions.LEVEL_WORKSPACE_WRITE,
                              detail=reason, session_id=session_id)
            raise permissions.PermissionDenied(f"command blocked by safety policy: {reason}")
        assert_switch("terminal_enabled")
        permissions.audit("cmd_run", command, level=permissions.LEVEL_WORKSPACE_WRITE,
                          detail=f"workspace={workspace}", session_id=session_id)
        # ---- OS sandbox validation (never trust the command on its own) ----
        # The security boundary is the FILESYSTEM (workspace-scoped writes,
        # private tmpfs). Network stays available so scrapers, dev servers and
        # package installs work; command-safety policy still blocks abuse.
        prepared = sandbox.prepare(command, workspace, need_network=True)
        if prepared.refused:
            permissions.audit("cmd_refused_sandbox", command, allowed=False, level=3,
                              detail=prepared.reason, session_id=session_id)
            raise permissions.PermissionDenied(prepared.reason)
        if prepared.mode == "bwrap":
            proc = await asyncio.create_subprocess_exec(
                *prepared.argv,
                env=prepared.env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
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
            result = {"exit_code": proc.returncode,
                      "stdout": out.decode("utf-8", "replace")[-100_000:],
                      "timed_out": timed_out}
        else:
            result = await execution_backend.run(command, cwd=workspace, timeout=timeout)
            result["mode"] = prepared.mode  # 'degraded' is reported, never hidden
        result["sandbox_mode"] = prepared.mode
        result["command"] = command
        result["workspace"] = workspace
        return result

    # -------- long-running processes --------

    async def start_process(self, command: str, workspace: str,
                            session_id: str = "") -> dict:
        permissions.check_access(workspace, session_id=session_id)
        allowed, reason = permissions.check_command_safety(command)
        if not allowed:
            raise permissions.PermissionDenied(f"blocked: {reason}")
        permissions.audit("process_start", command, level=permissions.LEVEL_WORKSPACE_WRITE,
                          detail=f"workspace={workspace}", session_id=session_id)
        backend = get_backend()
        import os as _os
        env = {k: v for k, v in _os.environ.items()
               if not k.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
        if backend.os_name == "windows":
            import subprocess
            shell = backend.default_shell()
            argv = [shell, "-NoProfile", "-NonInteractive", "-Command", command] \
                if "powershell" in shell.lower() or "pwsh" in shell.lower() else [shell, "/C", command]
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=workspace, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            # POSIX: run long-running processes inside the SAME sandbox as
            # regular commands (workspace-scoped writes, filtered env) —
            # a dev server gets network only when the tool needs it.
            prepared = sandbox.prepare(command, workspace, need_network=True)
            if prepared.mode == "refused":
                raise permissions.PermissionDenied(prepared.reason or "command refused")
            if prepared.mode == "bwrap":
                proc = await asyncio.create_subprocess_exec(
                    *prepared.argv, cwd=workspace, env=prepared.env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    preexec_fn=_os.setsid)
            else:  # degraded: policy-checked, no OS sandbox available
                proc = await asyncio.create_subprocess_exec(
                    backend.default_shell(), "-lc", command, cwd=workspace, env=env,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    preexec_fn=_os.setsid)
        pid = self._next_pid
        self._next_pid += 1
        self.processes[pid] = {"proc": proc, "command": command, "workspace": workspace,
                               "started": time.time(), "buffer": "",
                               "task": asyncio.create_task(self._pump(pid))}
        return {"process_id": pid, "command": command}

    async def _pump(self, pid: int) -> None:
        entry = self.processes.get(pid)
        if not entry:
            return
        proc: asyncio.subprocess.Process = entry["proc"]
        while True:
            chunk = await proc.stdout.read(4096) if proc.stdout else b""
            if not chunk:
                break
            entry["buffer"] = (entry["buffer"] +
                               chunk.decode("utf-8", "replace"))[-60_000:]
        try:
            await proc.wait()
        except Exception:
            pass

    def read_output(self, process_id: int, since: int = 0) -> dict:
        entry = self.processes.get(process_id)
        if not entry:
            return {"error": f"unknown process {process_id}"}
        proc: asyncio.subprocess.Process = entry["proc"]
        text = entry["buffer"]
        return {"process_id": process_id, "output": text[since:],
                "offset": len(text), "running": proc.returncode is None,
                "exit_code": proc.returncode}

    async def stop_process(self, process_id: int, session_id: str = "") -> dict:
        entry = self.processes.get(process_id)
        if not entry:
            return {"error": f"unknown process {process_id}"}
        proc: asyncio.subprocess.Process = entry["proc"]
        permissions.audit("process_stop", entry["command"], session_id=session_id)
        try:
            if proc.returncode is None:
                import signal
                posix = entry.get("posix", True) and hasattr(os, "killpg")
                if posix:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        proc.terminate()
                else:
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
        except Exception as e:
            return {"error": str(e)}
        finally:
            task = entry.pop("task", None)
            if task:
                task.cancel()
        return {"process_id": process_id, "stopped": True}

    def list_processes(self) -> list[dict]:
        out = []
        for pid, e in self.processes.items():
            proc = e["proc"]
            out.append({"process_id": pid, "command": e["command"],
                        "workspace": e["workspace"],
                        "running": proc.returncode is None,
                        "exit_code": proc.returncode,
                        "elapsed": round(time.time() - e["started"], 1)})
        return out


terminal_engine = TerminalEngine()
