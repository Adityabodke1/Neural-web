"""Forever memory (profile facts), style mirroring and history retrieval.

Run: .venv/bin/python -m pytest backend/tests/test_long_memory.py -v
"""
from __future__ import annotations

import json

import pytest

from backend.app import conversations as conv
from backend.app import long_memory as lm


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(lm, "PROFILE_PATH", tmp_path / "profile.json")
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")
    monkeypatch.setattr(lm, "_BACKFILLED", False)
    return tmp_path


# ------------------------------------------------------------- names/facts
def test_ai_name_and_user_name_are_remembered(mem):
    lm.observe("Your name is Riya from now on")
    lm.observe("my name is Aditya")
    names = lm.names()
    assert names["ai_name"] == "Riya"
    assert names["user_name"] == "Aditya"
    block = lm.profile_block()
    assert "Riya" in block and "Aditya" in block


def test_hinglish_names_and_explicit_memory(mem):
    lm.observe("tumhara naam Neura hai")
    lm.observe("yaad rakhna ki kal demo hai")
    assert lm.names()["ai_name"] == "Neura"
    facts = lm._load()["facts"]
    assert any("kal demo hai" in f["text"] for f in facts)


def test_remember_and_dedupe(mem):
    assert lm.remember("User has a social exam on 25 Nov")
    assert lm.remember("User has a social exam on 25 Nov") == ""
    lm.observe("remember that I prefer dark mode")
    lm.observe("remember that I prefer dark mode")
    texts = [f["text"] for f in lm._load()["facts"]]
    assert texts.count("I prefer dark mode") == 1
    assert "User has a social exam on 25 Nov" in texts


def test_preferences_favourite_and_project(mem):
    lm.observe("I love mango shakes")
    lm.observe("my favourite language is Python")
    lm.observe("I'm working on a calculator app")
    texts = [f["text"] for f in lm._load()["facts"]]
    assert "User loves mango shakes" in texts
    assert "User's favourite language is Python" in texts
    assert "User is working on a calculator app" in texts


def test_facts_are_permanent_and_searchable_by_query(mem):
    lm.remember("User visited Goa in December 2024")
    for i in range(50):
        lm.remember(f"filler fact number {i}")
    block = lm.profile_block("tell me about Goa")
    assert "Goa" in block
    # storage keeps everything (display is capped)
    assert len(lm._load()["facts"]) == 51


def test_forget_removes_matching_facts(mem):
    lm.remember("User loves mangoes")
    lm.remember("User hates traffic")
    assert lm.forget("mango") == 1
    texts = [f["text"] for f in lm._load()["facts"]]
    assert texts == ["User hates traffic"]


def test_observe_never_raises_on_garbage(mem):
    assert lm.observe("") == []
    assert lm.observe("random words with no memory trigger") == []


# ----------------------------------------------------------------- style
def _write_history(rows):
    conv.CONV_PATH.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_style_profile_mirrors_lowercase_hinglish_and_emoji(mem):
    rows = [
        {"ts": "2026-11-20T20:00:00", "role": "user",
         "content": "yaar ye build fail ho raha hai 😭"},
        {"ts": "2026-11-20T20:01:00", "role": "assistant", "content": "ok"},
        {"ts": "2026-11-20T20:02:00", "role": "user",
         "content": "bhai kya karu ab"},
        {"ts": "2026-11-20T20:03:00", "role": "user", "content": "dekh na please"},
        {"ts": "2026-11-20T20:04:00", "role": "user", "content": "code chal raha hai?"},
        {"ts": "2026-11-20T20:05:00", "role": "user", "content": "thik hai 😅"},
        {"ts": "2026-11-20T20:06:00", "role": "user", "content": "ab bata"},
    ]
    _write_history(rows)
    style = lm.style_profile()
    assert "LEARNED STYLE" in style
    assert "Hinglish" in style
    assert "emoji" in style
    assert "Mirror" in style


def test_style_profile_needs_enough_messages(mem):
    _write_history([{"ts": "2026-11-20T20:00:00", "role": "user",
                     "content": "hi"}])
    assert lm.style_profile() == ""


# ------------------------------------------------------------- retrieval
def test_relevant_turns_finds_old_matches(mem):
    rows = [{"ts": "2026-11-01T10:00:00", "role": "user",
             "content": "the kubernetes deployment is broken on staging"},
            {"ts": "2026-11-01T10:01:00", "role": "assistant",
             "content": "check the ingress logs"}]
    for i in range(18):
        rows.append({"ts": f"2026-11-20T20:{i:02d}:00", "role": "user",
                     "content": f"recent filler {i}"})
    _write_history(rows)
    hits = lm.relevant_turns("what about the kubernetes deployment?",
                             skip_last=16)
    assert hits
    assert "kubernetes" in hits[0]["text"]


def test_relevant_turns_ignores_one_word_chatter(mem):
    rows = [{"ts": "2026-11-01T10:00:00", "role": "user",
             "content": "unrelated old text"}]
    rows += [{"ts": "2026-11-20T20:00:00", "role": "user",
              "content": "recent"} for _ in range(18)]
    _write_history(rows)
    assert lm.relevant_turns("ok", skip_last=16) == []


def test_long_term_block_combines_everything(mem):
    lm.observe("Your name is Riya")
    lm.observe("I love mango shakes")
    rows = [{"ts": "2026-11-01T10:00:00", "role": "user",
             "content": "remember the kubernetes deployment failure"}]
    for i in range(18):
        rows.append({"ts": f"2026-11-20T20:{i:02d}:00", "role": "user",
                     "content": f"recent message {i}"})
    _write_history(rows)
    block = lm.long_term_block("check the kubernetes deployment", skip_last=16)
    assert "LONG-TERM MEMORY" in block
    assert "Riya" in block
    assert "mango shakes" in block
    assert "RELEVANT OLDER TURNS" in block
    assert "kubernetes" in block
    assert "LEARNED STYLE" in block
    assert "STYLE MIRRORING" in block


def test_backfill_learns_existing_history_once(mem):
    rows = [
        {"ts": "2026-11-01T10:00:00", "role": "user",
         "content": "Your name is Riya"},
        {"ts": "2026-11-01T10:01:00", "role": "user",
         "content": "remember that my demo is on Friday"},
    ]
    _write_history(rows)
    learned = lm.backfill()
    assert learned >= 2
    assert lm.names()["ai_name"] == "Riya"
    assert any("demo is on Friday" in f["text"] for f in lm._load()["facts"])
    assert lm.backfill() == 0                    # only once per process
