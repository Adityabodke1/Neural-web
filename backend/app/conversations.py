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

DATED_MEMORY_GUIDANCE = (
    "DATED MEMORY: recalled messages below are prefixed with the real date and "
    "time they were said (for example '[yesterday, 21:04] ...'). Treat those "
    "labels as ground truth and never invent a date or time. Be time-aware and "
    "proactive: when something was discussed yesterday, earlier this week or on "
    "a specific date, bring it up naturally and ask how it went — e.g. "
    "\"Yesterday you said the build was failing — did you get it fixed?\" or "
    "\"What happened with that demo you mentioned last Friday?\". Use short, "
    "natural callbacks; do not list the timestamps mechanically. If the most "
    "recent recalled turn is from yesterday or earlier and mentions unfinished "
    "work, open your reply with a brief, natural follow-up about it."
)


def time_context(now: datetime | None = None) -> str:
    """Exact current local date/time for the model (never guessed)."""
    local = (now or datetime.now()).astimezone()
    tz = local.strftime("%z") or "+0000"
    tz = f"{tz[:3]}:{tz[3:]}"
    return (
        "CURRENT DATE/TIME (trust this over any assumption): "
        f"{local.strftime('%A, %d %B %Y, %I:%M %p')} "
        f"({local.tzname() or 'local time'}, UTC{tz})."
    )


def memory_context(base_prompt: str, now: datetime | None = None) -> str:
    """System prompt + dated-memory rules + the exact current date/time."""
    return f"{base_prompt}\n\n{DATED_MEMORY_GUIDANCE}\n{time_context(now)}"


def format_when(ts: str, now: datetime | None = None) -> str:
    """Human label for a stored ISO timestamp: today/yesterday/N days/date."""
    try:
        dt = datetime.fromisoformat(str(ts))
    except Exception:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    ref = now or datetime.now()
    if ref.tzinfo is not None:
        ref = ref.astimezone().replace(tzinfo=None)
    days = (ref.date() - dt.date()).days
    if days == 0:
        label = "today"
    elif days == 1:
        label = "yesterday"
    elif 2 <= days <= 6:
        label = f"{days} days ago"
    else:
        label = dt.strftime("%a %d %b %Y")
    return f"{label}, {dt.strftime('%H:%M')}"


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


def recent_messages(n_turns: int = 8, with_time: bool = False,
                    now: datetime | None = None) -> list[dict]:
    """Last N conversational turns as model-context messages (oldest first).

    With ``with_time=True`` each message is prefixed with its real timestamp
    label so the model can reason about dates and ask follow-ups like
    "what happened yesterday?".
    """
    recs = list(_iter_records())
    picked = recs[-max(1, n_turns) * 2:]
    out: list[dict] = []
    for r in picked:
        content = r["content"]
        if with_time:
            when = format_when(r.get("ts", ""), now)
            if when:
                content = f"[{when}] {content}"
        out.append({"role": r["role"], "content": content})
    return out


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
