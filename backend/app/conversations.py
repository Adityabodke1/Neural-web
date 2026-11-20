"""Durable conversation history shared by the shell, chat and voice modes.

Every user message and assistant reply is appended to ONE append-only file:

    ~/.neuralweb/conversations.jsonl      (one JSON object per line)

On startup the recent turns are replayed into the model context, so the bot
remembers earlier conversations and reacts to them naturally. Logging never
raises — a failed write must never break a conversation.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

CONV_DIR = Path.home() / ".neuralweb"
CONV_PATH = CONV_DIR / "conversations.jsonl"

MAX_CONTENT = 8000          # keep one huge pasted reply from bloating the log


def log_turn(role: str, content: str, session: str = "shell",
             workspace: str = "") -> None:
    """Append one message to the durable conversation file (never raises)."""
    text = str(content or "").strip()
    if not text:
        return
    try:
        CONV_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "session": session,
            "workspace": workspace,
            "role": role,
            "content": text[:MAX_CONTENT],
        }
        with CONV_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _iter_records():
    """Yield valid records, silently skipping blank/corrupt lines."""
    try:
        with CONV_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue          # never let one bad line kill history
                if isinstance(rec, dict) and rec.get("role") in ("user", "assistant"):
                    yield rec
    except FileNotFoundError:
        return
    except Exception:
        return


def recent_messages(n_turns: int = 8) -> list[dict]:
    """Last N conversational turns as model-context messages (oldest first)."""
    recs = list(_iter_records())
    picked = recs[-max(1, n_turns) * 2:]
    return [{"role": r["role"], "content": r["content"]} for r in picked]


def recent_turns(limit: int = 20, session: str | None = None) -> list[dict]:
    """Last `limit` records with timestamps (for the `conversations` view).

    Pass `session` to show one surface only (e.g. the app chat thread) while
    the model still recalls every surface through `recent_messages`.
    """
    recs = [r for r in _iter_records()
            if session is None or r.get("session") == session]
    return recs[-limit:]


def clear_session(session: str = "app") -> int:
    """Remove one surface's turns, keeping every other session's memory.

    Returns the number of records removed. Rewrites via a temp file so a
    crash can never truncate the history mid-write.
    """
    try:
        if not CONV_PATH.exists():
            return 0
        kept: list[str] = []
        removed = 0
        with CONV_PATH.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                except Exception:
                    kept.append(stripped)          # keep unparseable lines
                    continue
                if isinstance(rec, dict) and rec.get("session") == session:
                    removed += 1
                else:
                    kept.append(stripped)
        CONV_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONV_PATH.with_suffix(".tmp")
        tmp.write_text("".join(k + "\n" for k in kept), encoding="utf-8")
        tmp.replace(CONV_PATH)
        return removed
    except Exception:
        return 0


def stats() -> dict:
    """Cheap counters for the startup line / `neural-web conversations`."""
    recs = list(_iter_records())
    return {
        "messages": len(recs),
        "turns": sum(1 for r in recs if r.get("role") == "user"),
        "last": recs[-1]["ts"] if recs else "",
        "path": str(CONV_PATH),
    }
