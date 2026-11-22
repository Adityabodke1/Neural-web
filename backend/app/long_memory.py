"""Long-term, forever memory + learned writing style for NEURAL WEB.

Two layers:

1. Durable facts — names, preferences, projects and explicit "remember ..."
   statements. Stored in ``~/.neuralweb/profile.json`` and kept forever.
2. Full conversation history — every turn already lives in
   ``~/.neuralweb/conversations.jsonl``. For each new message we retrieve the
   older turns that share meaningful words, so anything the user ever said can
   come back when it is relevant (not just the last eight turns).

It also derives a compact style profile from the user's own messages, so
replies mirror their language mix, length, casing, slang and emoji habits —
with a little warmth/sugarcoating.

This module never raises and never blocks a reply: storage failures are
silent and retrieval is best-effort.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import conversations as conv

PROFILE_PATH = Path.home() / ".neuralweb" / "profile.json"

MAX_FACT = 240
MAX_FACTS = 1000                 # stored forever in practice; display is capped
MAX_MATCHING_FACTS = 12
MAX_RELEVANT_TURNS = 6

_NAME_STOP = {"a", "an", "the", "not", "my", "your", "later", "when", "if",
              "tomorrow", "back", "now", "please", "ok", "okay"}
_NAME_BAD = {"you", "me", "him", "her", "them", "it", "this", "that"}

_AI_NAME = re.compile(
    r"(?i)(?:your name is|your name should be|you are called|you're called|"
    r"i(?:'ll| will) call you|i(?:'ll| will) name you|i name you|"
    r"tumhara naam|tumhara name)\s+([^\n.!?,;]{1,40})")
_USER_NAME = re.compile(
    r"(?i)(?:my name is|mera naam|call me)\s+([^\n.!?,;]{1,30})")
_REMEMBER = re.compile(
    r"(?i)\b(?:remember(?: that)?|don'?t forget(?: that)?|"
    r"keep in mind(?: that)?|yaad rakhna|note that)\b[:\s]+(.{3,240})")
_LIKE = re.compile(
    r"(?i)\bi (?:really |honestly |usually )?"
    r"(like|love|enjoy|prefer|hate|dislike)\s+([^.!?\n]{2,120})")
_FAVOURITE = re.compile(
    r"(?i)\bmy favou?rite\s+([\w ]{1,30})\s+is\s+([^.!?\n]{1,80})")
_BUILDING = re.compile(
    r"(?i)\bi(?:'m| am)\s+(?:working on|building|making)\s+([^.!?\n]{2,100})")

_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
_HINGLISH = {
    "yaar", "bhai", "bro", "kya", "hai", "nahi", "nhi", "kar", "karo",
    "raha", "rahi", "mera", "meri", "tum", "aap", "acha", "accha", "thik",
    "theek", "matlab", "bohot", "bahut", "chalo", "haan", "kyun", "kaise",
    "abhi", "kal", "aaj", "bata", "batao", "dekh", "dekho",
}
_STOP = set("""
the a an and or but if then so to of in on for with at by from is are was
were be been being i you he she it we they me him her them my your his its
our their this that these those what why how when where who whom which not no
yes do does did have has had can could will would should just really very too
also about get got going go want need like ok okay hey hi hello please thanks
there here now then than as into over under out up down off again once all any
both each few more most other some such only own same very s t don now
""".split())


# ---------------------------------------------------------------- storage
def _blank() -> dict:
    return {"ai_name": "", "user_name": "", "facts": [], "updated": ""}


def _load() -> dict:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            prof = _blank()
            prof.update(data)
            if not isinstance(prof.get("facts"), list):
                prof["facts"] = []
            return prof
    except Exception:
        pass
    return _blank()


def _save(prof: dict) -> None:
    try:
        PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        prof["updated"] = datetime.now().isoformat(timespec="seconds")
        tmp = PROFILE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(PROFILE_PATH)
    except Exception:
        pass


def _clean_name(raw: str) -> str:
    raw = re.split(r"\b(?:from now|and|but|ok|okay|please)\b", raw,
                   maxsplit=1, flags=re.IGNORECASE)[0]
    raw = raw.strip().strip("\"'`").strip()
    words = raw.split()
    while words and words[-1].lower() in {"hai", "hain", "h", "ho", "raha",
                                          "rahi", "kar", "karo", "bolo",
                                          "bulao", "ka", "ki"}:
        words.pop()
    if not words:
        return ""
    if words[0].lower() in _NAME_STOP or words[0].lower() in _NAME_BAD:
        return ""
    return " ".join(words[:3])[:30]


def names() -> dict:
    """Stored names: {'ai_name': ..., 'user_name': ...}."""
    prof = _load()
    return {"ai_name": str(prof.get("ai_name") or ""),
            "user_name": str(prof.get("user_name") or "")}


def remember(text: str, kind: str = "fact") -> str:
    """Store one permanent fact (deduped). Returns the stored text or ''."""
    text = re.sub(r"\s+", " ", str(text or "")).strip(" .")
    if len(text) < 2:
        return ""
    text = text[:MAX_FACT]
    prof = _load()
    low = text.lower()
    for f in prof["facts"]:
        if str(f.get("text", "")).lower() == low:
            return ""
    prof["facts"].append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": kind[:24],
        "text": text,
    })
    prof["facts"] = prof["facts"][-MAX_FACTS:]
    _save(prof)
    return text


def forget(term: str) -> int:
    """Remove facts containing `term`. Returns how many were removed."""
    term = (term or "").strip().lower()
    if not term:
        return 0
    prof = _load()
    before = len(prof["facts"])
    prof["facts"] = [f for f in prof["facts"]
                     if term not in str(f.get("text", "")).lower()]
    removed = before - len(prof["facts"])
    if removed:
        _save(prof)
    return removed


# ------------------------------------------------------- fact extraction
def observe(user_text: str) -> list[str]:
    """Learn names, preferences and explicit memories from one user message."""
    text = str(user_text or "").strip()
    if not text:
        return []
    learned: list[str] = []
    prof = _load()
    changed = False

    m = _AI_NAME.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name and name != prof.get("ai_name"):
            prof["ai_name"] = name
            changed = True
            learned.append(f"AI name: {name}")

    m = _USER_NAME.search(text)
    if m:
        name = _clean_name(m.group(1))
        if name and name != prof.get("user_name"):
            prof["user_name"] = name
            changed = True
            learned.append(f"user name: {name}")

    for m in _REMEMBER.finditer(text):
        fact = remember(m.group(1), kind="explicit")
        if fact:
            learned.append(fact)

    for m in _LIKE.finditer(text):
        verb, obj = m.group(1).lower(), m.group(2).strip()
        if obj.lower().startswith(("you to", "to ", "when ", "if ")):
            continue
        verb = {"like": "likes", "love": "loves", "enjoy": "enjoys",
                "prefer": "prefers", "hate": "hates",
                "dislike": "dislikes"}[verb]
        fact = remember(f"User {verb} {obj}", kind="preference")
        if fact:
            learned.append(fact)

    for m in _FAVOURITE.finditer(text):
        fact = remember(f"User's favourite {m.group(1).strip()} is "
                        f"{m.group(2).strip()}", kind="preference")
        if fact:
            learned.append(fact)

    for m in _BUILDING.finditer(text):
        fact = remember(f"User is working on {m.group(1).strip()}",
                        kind="project")
        if fact:
            learned.append(fact)

    if changed:
        _save(prof)
    return learned


# ------------------------------------------------------------ prompt text
def _tokens(text: str) -> set[str]:
    words = re.findall(r"[A-Za-z\u0900-\u097F]{3,}", str(text or "").lower())
    return {w for w in words if w not in _STOP}


def _matching_facts(prof: dict, query: str) -> list[dict]:
    q = _tokens(query)
    if not q:
        return []
    hits = []
    for f in prof["facts"]:
        shared = q & _tokens(f.get("text", ""))
        if shared:
            hits.append(f)
    return hits[-MAX_MATCHING_FACTS:]


def profile_block(query: str = "") -> str:
    prof = _load()
    facts = prof["facts"]
    lines: list[str] = ["LONG-TERM MEMORY (permanent — never contradict):"]
    if prof.get("ai_name"):
        lines.append(f"- The user named you \"{prof['ai_name']}\"; "
                     f"respond to that name and use it naturally.")
    if prof.get("user_name"):
        lines.append(f"- The user's name is {prof['user_name']}; "
                     f"address them by it sometimes.")
    if not facts:
        lines.append("- (no stored facts yet — learn as you talk)")
    else:
        older = _matching_facts(prof, query)
        recent = facts[-40:]
        shown: list[dict] = []
        seen: set[str] = set()
        for f in older + recent:
            key = str(f.get("text", "")).lower()
            if key and key not in seen:
                seen.add(key)
                shown.append(f)
        for f in shown[-52:]:
            when = conv.format_when(f.get("ts", ""))
            prefix = f"[{when}] " if when else ""
            lines.append(f"- {prefix}{f.get('text', '')}")
        if len(facts) > len(shown):
            lines.append(f"- ... {len(facts) - len(shown)} older facts are "
                         f"still stored and searchable when relevant.")
    return "\n".join(lines)


_STYLE_CACHE: dict[str, str] = {}


def style_profile(limit: int = 200) -> str:
    """Derive a compact description of how the user writes."""
    recs = [r for r in conv._iter_records() if r.get("role") == "user"][-limit:]
    if len(recs) < 5:
        return ""
    texts = [str(r.get("content", "")).strip() for r in recs if r.get("content")]
    if len(texts) < 5:
        return ""
    n = len(texts)
    words = [len(t.split()) for t in texts]
    avg = sum(words) / n
    lower = sum(1 for t in texts
                if t[:1].islower() and not t.startswith(("I ", "I'", "I,"))) / n
    emoji = sum(1 for t in texts if _EMOJI.search(t)) / n
    hinglish = sum(1 for t in texts
                   if any(w in _HINGLISH for w in re.findall(r"[a-z]+",
                                                             t.lower()))) / n
    deva = sum(1 for t in texts if _DEVANAGARI.search(t)) / n
    questions = sum(1 for t in texts if "?" in t) / n
    exclaims = sum(1 for t in texts if "!" in t) / n
    ellipsis = sum(1 for t in texts if "..." in t) / n

    bits: list[str] = []
    if avg <= 6:
        bits.append("very short messages")
    elif avg <= 14:
        bits.append("short-to-medium messages")
    else:
        bits.append("longer, detailed messages")
    if lower >= 0.6:
        bits.append("mostly lowercase")
    if emoji >= 0.2:
        bits.append("frequent emojis")
    elif emoji >= 0.05:
        bits.append("occasional emojis")
    if deva >= 0.2:
        bits.append("writes Hindi in Devanagari")
    if hinglish >= 0.2:
        bits.append("mixes English with Hinglish (yaar, bhai, kya…)")
    if questions >= 0.3:
        bits.append("often asks direct questions")
    if exclaims >= 0.25:
        bits.append("uses exclamation marks")
    if ellipsis >= 0.15:
        bits.append("uses '...' for pauses")

    freq: dict[str, int] = {}
    for t in texts:
        for w in _tokens(t):
            if len(w) > 12 or w.isdigit():
                continue
            freq[w] = freq.get(w, 0) + 1
    topics = [w for w, c in sorted(freq.items(), key=lambda x: -x[1])[:5]
              if c >= 3]
    line = ("LEARNED STYLE (from the user's own messages): they write "
            + ", ".join(bits) + ".")
    if topics:
        line += " Recurring topics: " + ", ".join(topics) + "."
    line += (" Mirror this rhythm, vocabulary and language mix — the vibe, not "
             "typos. If they write Hinglish, reply in natural Hinglish; if "
             "Hindi, Hindi; if English, English.")
    return line


def relevant_turns(query: str, limit: int = MAX_RELEVANT_TURNS,
                   skip_last: int = 16) -> list[dict]:
    """Search the ENTIRE history for older turns relevant to this message."""
    q = _tokens(query)
    if not q:
        return []
    try:
        recs = list(conv._iter_records())
    except Exception:
        return []
    old = recs[:-skip_last] if len(recs) > skip_last else []
    if not old:
        return []
    scored: list[tuple[int, dict]] = []
    for rec in old:
        shared = q & _tokens(rec.get("content", ""))
        if shared:
            scored.append((len(shared), rec))
    if not scored:
        return []
    top = sorted(scored, key=lambda x: (x[0], x[1].get("ts", "")),
                 reverse=True)[:limit]
    top.sort(key=lambda x: x[1].get("ts", ""))
    return [{"ts": r.get("ts", ""), "role": r.get("role", ""),
             "text": str(r.get("content", ""))[:420]} for _, r in top]


STYLE_AND_WARMTH = (
    "STYLE MIRRORING & WARMTH: reply in the user's own chatting style (message "
    "length, casing, punctuation, slang, emoji frequency, Hinglish/Hindi/"
    "English mix). Add a little sugarcoating for a better experience: warm, "
    "encouraging, a light compliment or empathy where natural, and soften bad "
    "news ('almost there — one small fix left') while staying completely honest. "
    "Never invent praise or hide a real problem."
)


def long_term_block(user_text: str = "", skip_last: int = 16) -> str:
    """Everything durable to inject for this turn: facts, old turns, style."""
    parts = [profile_block(user_text)]
    old = relevant_turns(user_text, skip_last=skip_last)
    if old:
        lines = ["RELEVANT OLDER TURNS (retrieved from the full history):"]
        for r in old:
            when = conv.format_when(r["ts"]) or r["ts"]
            who = "user" if r["role"] == "user" else "assistant"
            lines.append(f"- [{when}] {who}: {r['text']}")
        parts.append("\n".join(lines))
    style = style_profile()
    if style:
        parts.append(style)
    parts.append(STYLE_AND_WARMTH)
    return "\n\n".join(parts)


# ------------------------------------------------------------- bootstrap
_BACKFILLED = False


def backfill() -> int:
    """Teach the profile from existing history once per process."""
    global _BACKFILLED
    if _BACKFILLED:
        return 0
    _BACKFILLED = True
    learned = 0
    try:
        for rec in conv._iter_records():
            if rec.get("role") == "user":
                learned += len(observe(rec.get("content", "")))
    except Exception:
        pass
    return learned
