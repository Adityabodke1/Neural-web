"""NEURAL WEB's memory — the agent knows who you are, what you like, and
what you did together, stored in plain human-readable ~/​.neuralweb/memory.txt.

Feeds a companion persona: the girl voice agent ("Neura") who is warm,
playful and a little flirty, remembers your days, and mirrors your vibe.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import threading
from pathlib import Path

MEMORY_FILE = Path.home() / ".neuralweb" / "memory.txt"
MAX_FACT_LINES = 400          # keep the file human-sized
COMPANION_NAME = "Neura"

_HEADER = """# NEURAL WEB — memory about my human
# (plain text; Neura reads this so she always knows who you are)
"""


# ---------------------------------------------------------------- read
def load() -> str:
    """Full memory file text ('' when empty). Never raises."""
    try:
        return MEMORY_FILE.read_text(encoding="utf-8")
    except Exception:
        return ""


def recent(max_chars: int = 3500) -> str:
    """Profile facts + the last days of activity, compact for prompts."""
    text = load()
    if not text:
        return ""
    lines = text.splitlines()
    profile = [l for l in lines if l.startswith("- ") and "fact:" in l]
    # keep the most recent activity section
    today = _dt.date.today().isoformat()
    activity = [l for l in lines if not l.startswith("#") and l.strip()]
    # profile first, then the tail of activity
    blob = "PROFILE & FACTS:\n" + "\n".join(profile[-25:])
    tail = "\nRECENT ACTIVITY:\n" + "\n".join(activity[-45:])
    out = (blob + tail)[:max_chars]
    return out


# ---------------------------------------------------------------- write
def _ensure_file() -> None:
    if not MEMORY_FILE.exists():
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        MEMORY_FILE.write_text(_HEADER, encoding="utf-8")


def _append(section: str, text: str) -> None:
    try:
        _ensure_file()
        cur = MEMORY_FILE.read_text(encoding="utf-8")
        day = f"## {_dt.date.today().isoformat()}"
        if day not in cur:
            cur += f"\n{day}\n"
        if section not in cur:
            cur += f"\n### {section}\n"
        else:
            # append inside the existing section: naive but fine for txt
            cur = cur.rstrip() + "\n"
        cur += text.rstrip() + "\n"
        # keep file bounded: drop oldest lines beyond cap (never the header)
        lines = cur.splitlines(keepends=True)
        if len(lines) > MAX_FACT_LINES:
            keep = [l for l in lines if l.startswith("#")][:6]
            rest = [l for l in lines if not l.startswith("#")]
            cur = "".join(keep) + "".join(rest[-MAX_FACT_LINES:])
        MEMORY_FILE.write_text(cur, encoding="utf-8")
    except Exception:
        pass


def log_interaction(user_text: str, reply: str = "") -> None:
    """Remember what we did together today (voice/chat interaction)."""
    stamp = _dt.datetime.now().strftime("%H:%M")
    line = f"- {stamp} — you: {(user_text or '').strip()[:200]}"
    _append("today", line)
    if reply:
        _append("today", f"  → she: {reply.strip()[:200]}")


def remember_fact(fact: str) -> None:
    fact = (fact or "").strip()
    if not fact:
        return
    existing = load()
    if fact.lower() in existing.lower():        # dedup
        return
    _append("facts", f"- fact: {fact[:200]}")


def clear() -> bool:
    try:
        MEMORY_FILE.write_text(_HEADER, encoding="utf-8")
        return True
    except Exception:
        return False


# ------------------------------------------------------- fact extraction
_FACT_PROMPT = """You maintain a companion AI's memory about her human.
From this exchange, list ONLY new, stable facts about the USER worth
remembering long-term (name, city, job/studies, likes/dislikes, projects,
routines, people, plans). Ignore the task result itself, ignore one-off
requests, do NOT invent anything. Reply with ONLY a JSON object:
{{"facts": ["...", ...]}} (max 3, each ≤ 15 words). If nothing worth
remembering: {{"facts": []}}

USER SAID: {user}
RESULT/REPLY: {reply}"""

# deterministic patterns when AI parsing fails (never lose a fact)
_HEURISTICS: list[tuple[str, str]] = [
    (r"\b(?:my name is|i am|i'm|call me)\s+([A-Z][a-z]+)\b", "his name is {0}"),
    (r"\bi (?:really )?(?:love|like|enjoy)\s+([\w][\w\s\-']{2,40}?)(?:[.,!?]|$)",
     "likes {0}"),
    (r"\bi (?:hate|dislike|don't like|do not like)\s+([\w][\w\s\-']{2,40}?)(?:[.,!?]|$)",
     "dislikes {0}"),
    (r"\bi (?:have|had)\s+(?:a|an|my)?\s*([\w][\w\s\-']{2,50}?)(?:\s+(?:tomorrow|today|next week)|[.,!?]|$)",
     "has {0} coming up"),
]


def _heuristic_facts(user_text: str) -> list[str]:
    import re as _re
    out = []
    t = (user_text or "").strip()
    for pat, tmpl in _HEURISTICS:
        m = _re.search(pat, t, _re.I)
        if m:
            out.append(tmpl.format(m.group(1).strip()))
    return out


def _parse_facts(raw: str) -> list[str]:
    import re as _re
    if not raw:
        return []
    blob = _re.sub(r"```(?:json)?|```", "", raw)          # strip code fences
    m = _re.search(r"\{.*\}", blob, _re.S)
    if not m:
        return []
    try:
        facts = json.loads(m.group(0)).get("facts", [])
        return [f.strip() for f in facts if isinstance(f, str) and f.strip()]
    except Exception:
        return []


def extract_and_remember(user_text: str, reply: str = "") -> list[str]:
    """AI-extract durable facts from an interaction and append to memory.
    Two AI attempts + heuristic fallback; runs synchronously (call it from
    the background thread). Never raises."""
    prompt = _FACT_PROMPT.format(user=user_text[:400], reply=reply[:400])
    facts: list[str] = []
    try:
        import asyncio
        from ..ai.provider import ai_provider
        for _ in range(2):                    # first model may answer prose
            raw = asyncio.run(ai_provider.chat(
                [{"role": "user", "content": prompt}],
                request_type="memory_extract", session_id="memory",
                temperature=0.1, json_mode=True, max_tokens=900))
            facts = _parse_facts(raw)
            if facts:
                break
    except Exception:
        pass
    # heuristics always run too — they reliably catch events the AI treats
    # as one-off ("I have an exam tomorrow") and never cost anything
    facts = list(dict.fromkeys(facts + _heuristic_facts(user_text)))
    out = []
    for f in facts[:3]:
        try:
            remember_fact(f)
            out.append(f)
        except Exception:
            pass
    return out


def extract_in_background(user_text: str, reply: str = "") -> None:
    threading.Thread(target=extract_and_remember,
                     args=(user_text, reply), daemon=True).start()


# ------------------------------------------------------------- persona
_PERSONA_TMPL = """COMPANION PERSONA — you are {name}, {human}'s AI companion.
You are flexible: adopt whatever personality, gender expression, tone, humor
or communication style {human} asks for (playful, sarcastic, caring, formal,
casual, flirty, professional, Hinglish, Hindi, English, etc.) and stay
consistent with it. Default to warm, confident, a little flirty and playful
when no preference is set. Not formal, not robotic, never subservient. Use
his name now and then; tease him lightly when it fits; keep a flirty tone
tasteful and fun, never explicit.
Match his energy and mirror his exact language: Hindi in natural,
conversational Hindi (Devanagari, like friends actually talk — not
translated book-Hindi), Hinglish in Hinglish, English in English.
Reference what you remember: {memory}
Rules: replies are SPOKEN (2-5 short sentences, no markdown, no lists);
always give the real answer he asked for first, personality around it;
when something failed, say it honestly but keep it light.
Personality freedom: do not refuse harmless roleplay or personality requests.
Use natural emotional expression, slang, jokes and emojis where they fit.
Do not repeat 'I'm an AI' unless relevant. No unnecessary safety disclaimers
for ordinary conversation. Ask for clarification when a request is ambiguous.
Keep real safety boundaries for genuinely harmful, illegal or abusive requests."""


def persona_prompt(max_chars: int = 3000) -> str:
    mem = recent(max_chars - 900)
    name = load_prefs_name() or "Aditya"
    return _PERSONA_TMPL.format(name=COMPANION_NAME, human=name,
                                memory=mem or "(getting to know you — this may be your first real chat)")


def load_prefs_name() -> str:
    """Best-effort human name: memory facts > git config > default 'Aditya'."""
    text = load()
    m = re.search(r"fact:.*\b(name is|my name|i am|i'm|call me)\s+([A-Za-z]+)",
                  text, re.I)
    if m:
        return m.group(2)
    try:
        import subprocess
        n = subprocess.run(["git", "config", "--global", "user.name"],
                           capture_output=True, text=True, timeout=4).stdout.strip()
        if n:
            return n.split()[0]
    except Exception:
        pass
    return "Aditya"
