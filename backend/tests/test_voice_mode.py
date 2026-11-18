"""Voice-mode / computer-use tests: app launcher whitelist, web tools, STT.

Run: .venv/bin/python -m pytest backend/tests/test_voice_mode.py -v
"""
from __future__ import annotations

import wave
import struct

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
    import backend.cli.commands.voice_cmd as v

    class FakeRec:
        def record(self, src):
            return object()

        def recognize_google(self, audio):
            import speech_recognition as sr
            raise sr.UnknownValueError()

    import speech_recognition as sr
    monkeypatch.setattr(sr.Recognizer, "record", lambda self, s: object())
    monkeypatch.setattr(sr.Recognizer, "recognize_google",
                        lambda self, a: (_ for _ in ()).throw(sr.UnknownValueError()))
    wav = _make_wav(tmp_wav(), seconds=0.3)
    assert v.transcribe_wav(wav) == ""


def test_transcribe_wav_mocked_success(monkeypatch):
    import backend.cli.commands.voice_cmd as v
    import speech_recognition as sr
    monkeypatch.setattr(sr.Recognizer, "record", lambda self, s: object())
    monkeypatch.setattr(sr.Recognizer, "recognize_google",
                        lambda self, a: "open brave and search amazon")
    wav = _make_wav(tmp_wav(), seconds=0.3)
    assert v.transcribe_wav(wav) == "open brave and search amazon"


# ------------------------------------------------------------ helpers
def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro) \
        if False else asyncio.run(coro)


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
