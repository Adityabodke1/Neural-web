"""Companion memory + persona + continuous voice loop + Gemini TTS tests.

Run: .venv/bin/python -m pytest backend/tests/test_companion.py -v
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ------------------------------------------------------- memory (memory.txt)
@pytest.fixture()
def mem_home(tmp_path, monkeypatch):
    from backend.app.voice import companion
    monkeypatch.setattr(companion, "MEMORY_FILE", tmp_path / "memory.txt")
    return companion


def test_log_interaction_creates_day_section(mem_home):
    c = mem_home
    c.log_interaction("open brave and search earbuds", "Found ₹1,999!")
    text = c.load()
    assert "open brave and search earbuds" in text
    assert "Found ₹1,999!" in text
    import datetime
    assert datetime.date.today().isoformat() in text
    assert (c.MEMORY_FILE).exists()


def test_remember_fact_and_dedup(mem_home):
    c = mem_home
    c.remember_fact("likes wireless earbuds")
    c.remember_fact("likes wireless earbuds")          # duplicate ignored
    text = c.load()
    assert text.lower().count("likes wireless earbuds") == 1


def test_recent_includes_facts_and_activity(mem_home):
    c = mem_home
    c.remember_fact("studies engineering")
    c.log_interaction("build me a website", "done!")
    r = c.recent()
    assert "studies engineering" in r and "build me a website" in r


def test_clear_and_forget(mem_home):
    c = mem_home
    c.remember_fact("likes pizza")
    c.log_interaction("hello", "hi")
    assert c.clear() is True
    assert "likes pizza" not in c.load()
    c.remember_fact("likes pizza")
    lines = [l for l in c.load().splitlines() if "pizza" not in l.lower()]
    c.MEMORY_FILE.write_text("\n".join(lines) + "\n")
    assert "pizza" not in c.load()


# ---------------------------------------------------------------- persona
def test_persona_prompt_contains_name_and_memory(mem_home):
    c = mem_home
    c.remember_fact("works on neural web every night")
    p = c.persona_prompt()
    assert "Neura" in p
    assert "flirt" in p.lower()
    assert "works on neural web every night" in p     # memory is injected


def test_persona_human_name_from_memory(mem_home):
    c = mem_home
    c.remember_fact("his name is Arjun")
    assert c.load_prefs_name() == "Arjun"


def test_agent_voice_prompt_has_persona_slot():
    from backend.app.computer.coding_agent import VOICE_COMPUTER_SYSTEM
    fmt = VOICE_COMPUTER_SYSTEM.format(
        os="linux", workspace="/tmp", prompt="hi", persona="PERSONA-MARKER",
        max_steps=10)
    assert "PERSONA-MARKER" in fmt
    assert "launch_app" in fmt                        # computer-use guidance


# --------------------------------------------------- continuous loop / exit
def test_exit_phrases():
    from backend.cli.commands.voice_cmd import is_exit_phrase
    for phrase in ("bye", "Bye!", "bye bye", "goodbye", "good night",
                   "alvida", "chalo bye", "exit", "see you"):
        assert is_exit_phrase(phrase), phrase
    for not_exit in ("search bye products on amazon", "type bye in a file",
                     "open brave and search earbuds", "bye bye birdie toy"):
        assert not is_exit_phrase(not_exit), not_exit


# ------------------------------------------------------------- gemini tts
def test_gemini_tts_creates_wav(monkeypatch, tmp_path):
    """Mocked REST: inlineData PCM → wav file written."""
    import base64, struct
    from backend.app.voice import speech
    monkeypatch.setattr(speech, "_gemini_key", lambda: "fake-key")

    pcm = struct.pack("<2000h", *range(-16000, 16000, 16))   # 2000 samples
    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({"candidates": [{"content": {"parts": [
                {"inlineData": {"data": base64.b64encode(pcm).decode()}}]}}]}
            ).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: FakeResp())
    out = tmp_path / "g.wav"
    ok = speech._gemini_tts("hello", str(out), voice="Leda", style="warm")
    assert ok and out.exists() and out.stat().st_size > 100


def test_gemini_tts_no_key_is_false(monkeypatch):
    from backend.app.voice import speech
    monkeypatch.setattr(speech, "_gemini_key", lambda: "")
    assert speech._gemini_tts("hello", "/tmp/never.wav") is False


def test_auto_voice_falls_back_to_edge_without_gemini(monkeypatch):
    from backend.app.voice import speech
    monkeypatch.setattr(speech, "_gemini_key", lambda: "")
    v, key = speech.resolve_voice("auto")
    assert key == "auto-edge" and v == "en-IN-NeerjaExpressiveNeural"


def test_auto_voice_uses_gemini_with_key(monkeypatch):
    from backend.app.voice import speech
    monkeypatch.setattr(speech, "_gemini_key", lambda: "k")
    v, key = speech.resolve_voice("auto")
    assert key == "auto-gemini" and v == "Leda"
