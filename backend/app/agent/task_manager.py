"""Task manager: live progress tracking + event streams for every research run.

The same event bus feeds the FastAPI SSE endpoint, the web dashboard and the
CLI live dashboard, so all three stay in sync.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from ..database import repository as repo

STEPS = [
    "queued", "planning", "searching", "ranking", "scraping", "extracting",
    "gap_analysis", "verifying", "reporting", "complete", "stopped", "failed",
]


class TaskManager:
    """Tracks one research session's progress and broadcasts events."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.created = time.monotonic()
        self.stop_requested = False
        self.step = "queued"
        self.current_action = ""
        self.current_url = ""
        self.events: deque[dict] = deque(maxlen=2000)
        self.subscribers: set[asyncio.Queue] = set()
        self.stats: dict[str, Any] = {
            "queries_completed": 0,
            "pages_discovered": 0,
            "pages_scraped": 0,
            "pages_failed": 0,
            "duplicates_skipped": 0,
            "records_extracted": 0,
            "records_verified": 0,
            "ai_calls": 0,
            "ai_tokens": 0,
            "errors": [],
        }

    # ------------- events -------------

    def event(self, type_: str, payload: dict | None = None, **extra: Any) -> dict:
        evt = {
            "session_id": self.session_id,
            "type": type_,
            "ts": repo.utcnow(),
            "step": self.step,
            "payload": {**(payload or {}), **extra},
        }
        self.events.append(evt)
        for q in list(self.subscribers):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
        return evt

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        for evt in list(self.events):  # replay history to late subscribers
            q.put_nowait(evt)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.subscribers.discard(q)

    # ------------- progress -------------

    def set_step(self, step: str, action: str = "", url: str = "") -> None:
        self.step = step
        self.current_action = action or self.current_action
        if url:
            self.current_url = url
        self.event("step", {"step": step, "action": self.current_action, "url": self.current_url})
        repo.update_session(self.session_id, status=step if step in
                            ("running", "planning", "searching", "scraping",
                             "extracting", "verifying", "reporting") else self.step)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.created

    def snapshot(self) -> dict:
        s = dict(self.stats)
        s.update({
            "step": self.step,
            "current_action": self.current_action,
            "current_url": self.current_url,
            "elapsed_seconds": round(self.elapsed, 1),
            "stop_requested": self.stop_requested,
        })
        return s

    def request_stop(self) -> bool:
        self.stop_requested = True
        self.event("stop_requested", {})
        return True


class TaskRegistry:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskManager] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str, create: bool = True) -> TaskManager | None:
        async with self._lock:
            tm = self._tasks.get(session_id)
            if tm is None and create:
                tm = TaskManager(session_id)
                self._tasks[session_id] = tm
            return tm

    def pop(self, session_id: str) -> TaskManager | None:
        return self._tasks.pop(session_id, None)


registry = TaskRegistry()
