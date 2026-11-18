"""Shared streaming runner for the autonomous coding agent.

Used by `neural-web coding`, chat mode and the interactive shell so plain
sentences like "build a simple website" reach the REAL agent (tools, sandbox,
filesystem) instead of a chatbot reply.
"""
from __future__ import annotations

import asyncio

from .theme import console
from .ui import render


def pick_workspace(hint: str = "") -> str | None:
    """Resolve an authorized workspace: explicit path → authorized folders.

    Returns None when nothing is available (caller decides how to ask).
    """
    from ...app.computer import permissions
    from pathlib import Path
    if hint:
        p = Path(hint).expanduser()
        if p.exists():
            return str(p)
    folders = [f["path"] for f in permissions.list_folders() if f.get("active")]
    return folders[0] if folders else None


def stream_agent_run(prompt: str, workspace_path: str,
                     max_steps: int = 40, model: str = "auto",
                     mode: str = "code") -> dict:
    """Start a coding task and stream live events until it finishes.

    Chatty output (Claude-Code style): narration lines, tool activity, then a
    short result panel with real paths and verification.
    """
    from ..app.agent.task_manager import registry
    from ..app.computer import coding_agent
    async def run() -> tuple[dict, dict]:
        res = await coding_agent.start_coding_task(prompt, workspace_path,
                                                   max_steps, model, mode)
        tm = await registry.get(f"code-{res['task_id']}")
        seen = 0
        while True:
            events = list(tm.events)
            while seen < len(events):
                evt = events[seen]
                seen += 1
                line = render.event_line(evt)
                if line.plain.strip():
                    console.print(line)
                if evt.get("type") == "done":
                    return tm.snapshot(), evt
            await asyncio.sleep(0.35)

    try:
        snap, evt = asyncio.run(run())
    except KeyboardInterrupt:
        console.print("[warn]interrupted — checkpoint preserved[/warn]")
        return {"status": "interrupted", "summary": "", "verification": ""}
    payload = evt.get("payload", {}) if evt else {}
    from rich.panel import Panel
    console.print(Panel(
        f"[bold]summary:[/] {payload.get('summary', '')[:1200]}\n"
        f"[muted]verification: {payload.get('verification', '')}[/muted]",
        title="Task result", border_style="magenta"))
    return {"status": payload.get("status", "done"),
            "summary": payload.get("summary", ""),
            "verification": payload.get("verification", ""),
            "task_id": snap.get("id") if snap else None}
