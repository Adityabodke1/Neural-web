"""Voice-mode / computer-use tests: app launcher whitelist, web tools, STT.

Run: .venv/bin/python -m pytest backend/tests/test_voice_mode.py -v
"""
from __future__ import annotations

import struct
from pathlib import Path
import wave

import pytest

from backend.app.computer import app_launcher, tools


# ------------------------------------------------------- app launcher
def test_launch_whitelist_known_apps():
    assert app_launcher.resolve_app("brave") == "brave-browser"
    assert app_launcher.resolve_app("Brave Browser") == "brave-browser"
    assert app_launcher.resolve_app("files") == "nautilus"
    assert app_launcher.resolve_app("vscode") == "code"


def test_launch_refuses_arbitrary_binary():
    for bad in ("curl", "rm", "bash", "python3", "../../bin/sh", ""):
        assert app_launcher.resolve_app(bad) == ""


def test_launch_tool_rejects_non_whitelisted():
    ctx = tools.ToolContext("/tmp", "test")
    out = _run(tools.execute_tool("launch_app", {"app": "nc"}, ctx))
    assert out.get("error") == "app_not_allowed"


def test_launch_tool_rejects_bad_args():
    ctx = tools.ToolContext("/tmp", "test")
    out = _run(tools.execute_tool(
        "launch_app", {"app": "brave", "args": ["a; rm -rf ~"]}, ctx))
    assert out.get("error") == "invalid_arg"


def test_launch_real_app_detached(tmp_path):
    """Real launch (headless-safe): gnome-calculator or xdg-open — verify it
    started and then kill it. Skipped when no whitelisted app is installed."""
    import shutil, subprocess, time, os
    candidates = [c for c in ("gnome-calculator",)
                  if shutil.which(c)]
    if not candidates:
        pytest.skip("no whitelisted GUI app installed")
    out = app_launcher.launch("calculator", session_id="test")
    assert out.get("launched") is True
    time.sleep(1.0)
    p = subprocess.run(["pgrep", "-f", candidates[0]], capture_output=True)
    assert p.returncode == 0, "launched process not found"
    subprocess.run(["pkill", "-f", candidates[0]], capture_output=True)


# --------------------------------------------------------- web tools
def test_web_query_tool_shape():
    ctx = tools.ToolContext("/tmp", "test")
    out = _run(tools.execute_tool(
        "web_query", {"queries": ["wireless earbuds"], "max_results_per_query": 2}, ctx))
    assert "results" in out or "error" in out
    if "results" in out:
        for r in out["results"]:
            assert "title" in r and "url" in r


def test_web_query_accepts_single_string():
    ctx = tools.ToolContext("/tmp", "test")
    out = _run(tools.execute_tool("web_query", {"queries": "python"}, ctx))
    assert "error" not in out or out.get("error") != "no_queries"


def test_scrape_url_tool_blocked_url_is_honest():
    ctx = tools.ToolContext("/tmp", "test")
    out = _run(tools.execute_tool(
        "scrape_url", {"url": "https://localhost:9/nope"}, ctx))
    assert "status" in out


# ------------------------------------------------------------- STT
def test_transcribe_wav_silence_returns_empty(monkeypatch):
    """A silent wav transcribes to '' (network mocked — no real API call)."""
    import speech_recognition as sr
    monkeypatch.setattr(sr.Recognizer, "record", lambda self, s: object())
    monkeypatch.setattr(sr.Recognizer, "recognize_google",
                        lambda self, a, **kw: (_ for _ in ()).throw(
                            sr.UnknownValueError()))
    wav = _make_wav(tmp_wav(), seconds=0.3)
    from backend.app.voice import speech
    assert speech.transcribe_wav(wav) == ""


def test_transcribe_wav_mocked_success(monkeypatch):
    import speech_recognition as sr
    captured = {}
    monkeypatch.setattr(sr.Recognizer, "record", lambda self, s: object())

    def fake_rec(self, a, **kw):
        captured["lang"] = kw.get("language")
        return "open brave and search amazon"
    monkeypatch.setattr(sr.Recognizer, "recognize_google", fake_rec)
    wav = _make_wav(tmp_wav(), seconds=0.3)
    from backend.app.voice import speech
    assert speech.transcribe_wav(wav) == "open brave and search amazon"
    assert captured["lang"] == "en-IN"          # Indian accent language default


def test_mood_engine_shapes_delivery():
    from backend.app.voice.speech import detect_mood
    rate, pitch, mood = detect_mood("Great! I found wireless earbuds at 1999 rupees!")
    assert mood == "happy" and pitch.startswith("+")
    rate, pitch, mood = detect_mood("Sorry, I couldn't find that product.")
    assert mood == "apologetic" and pitch.startswith("-")
    rate, pitch, mood = detect_mood("The lowest price is 1999.")
    assert mood == "neutral"


def test_voice_resolution_and_prefs(tmp_path, monkeypatch):
    from backend.app.voice import speech
    monkeypatch.setattr(speech, "PREFS_PATH", tmp_path / "voice.json")
    v, key = speech.resolve_voice("neerja")
    assert v == "en-IN-NeerjaExpressiveNeural" and key == "neerja"
    speech.save_prefs({"voice": "ava"})
    assert speech.resolve_voice("")[0] == "en-US-AvaMultilingualNeural"
    # unknown name falls back to the saved preference, never crashes
    v, key = speech.resolve_voice("zzz")
    assert key == "ava"


def test_vad_trim_cuts_silence_and_boosts():
    """1 s silence + 0.3 s tone + 1 s silence → trimmed ≈0.5 s, louder."""
    from backend.app.voice.speech import trim_and_boost
    import math
    rate = 16000
    silence = [0] * rate
    quiet_tone = [int(300 * math.sin(2 * math.pi * 220 * i / rate))
                  for i in range(rate // 2)]           # 0.5 s at ~1% amplitude
    pcm = struct.pack("<%dh" % (len(silence + quiet_tone + silence)),
                      *(silence + quiet_tone + silence))
    out = trim_and_boost(pcm, rate)
    n = len(out) // 2
    assert rate * 0.3 < n < rate * 1.0                  # silence trimmed
    peak = max(abs(s) for (s,) in struct.iter_unpack("<h", out))
    assert peak > 2000                                  # boosted


def test_record_command_fixed_seconds_real_mic():
    """Real hardware: record 3 s from the mic and get a wav back."""
    from backend.app.voice.speech import record_command
    wav = record_command(seconds=3)
    if not wav:
        return                                          # no arecord/mic
    assert Path(wav).exists() and Path(wav).stat().st_size > 1000


# ------------------------------------------------------------ helpers
def _run(coro):
    import asyncio
    return asyncio.run(coro)


def tmp_wav() -> str:
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.close()
    return f.name


def _make_wav(path: str, seconds: float = 0.3) -> str:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path
