"""Streaming TTS architecture tests: sentence streaming, barge-in player,
rate control, overlap prevention.

Run: .venv/bin/python -m pytest backend/tests/test_voice_stream.py -v
"""
from __future__ import annotations

import struct
import subprocess
import time
import wave
from pathlib import Path

import pytest

from backend.app.voice import speech


# ------------------------------------------------------------ splitter
def test_split_sentences_english():
    out = speech.split_sentences(
        "Hi Aditya! I found wireless earbuds on Amazon. The lowest price is "
        "₹1,999. Shall I open Brave for you?")
    assert out[0] == "Hi Aditya!"
    assert any("₹1,999" in s for s in out)
    assert len(out) == 4


def test_split_sentences_hindi_danda():
    out = speech.split_sentences("नमस्ते! मैं न्यूरा हूँ। आज मैंने तुम्हारे लिए "
                                 "अमेज़न पर ईयरबड्स की कीमत देखी है, और वह बहुत "
                                 "कम रुपये में मिल रहे हैं दोस्त।")
    assert any("नमस्ते" in s for s in out)
    assert any("ईयरबड्स" in s for s in out)


def test_split_sentences_merges_fragments():
    out = speech.split_sentences("Done. ₹50 saved. OK!")
    assert all(len(s) >= 8 for s in out)          # no awkward 2-word chunks


# ------------------------------------------------------------ rate control
def test_combine_rate_with_user_speed(tmp_path, monkeypatch):
    monkeypatch.setattr(speech, "PREFS_PATH", tmp_path / "voice.json")
    speech.save_prefs({"rate": "+20%"})
    assert speech._combine_rate("-3%") == "+17%"
    speech.save_prefs({"rate": "-30%"})
    assert speech._combine_rate("+0%") == "-30%"
    speech.save_prefs({"rate": "+999%"})           # clamped
    assert speech._combine_rate("+0%") in ("+50%", "+80%")


# ---------------------------------------------------------------- player
def test_player_cuts_previous_playback(monkeypatch, tmp_path):
    """No overlapping audio: a second play terminates the first process."""
    procs = []
    wavs = []
    for i in range(2):
        w = tmp_path / f"p{i}.wav"
        with wave.open(str(w), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 1600)
        wavs.append(str(w))

    class FakeProc:
        def __init__(self):
            self.killed = False
        def poll(self):
            return None if not self.killed else 0
        def wait(self, timeout=None):
            return 0
        def terminate(self):
            self.killed = True

    monkeypatch.setattr(speech, "_which", lambda n: "/bin/aplay" if n in
                        ("aplay", "paplay") else "")
    monkeypatch.setattr(speech.subprocess, "Popen",
                        lambda *a, **k: procs.append(FakeProc()) or procs[-1])
    # disable the real barge-in mic watcher in this test
    monkeypatch.setattr(speech, "_barge_in_watch", lambda *a, **k: None)

    speech.PLAYER.play(wavs[0])
    assert len(procs) == 1
    speech.PLAYER.play(wavs[1])
    assert len(procs) == 2
    assert procs[0].killed is True                  # first was cut off


def test_player_stop_interrupts(monkeypatch):
    speech.PLAYER.interrupted = False
    speech.PLAYER.stop()
    assert speech.PLAYER.interrupted is True


# ------------------------------------------------------------ streaming
def test_speak_stream_plays_all_sentences(monkeypatch, tmp_path):
    wav = tmp_path / "t.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 3200)
    monkeypatch.setattr(speech, "_synth_wav",
                        lambda *a, **k: str(wav))
    speech.PLAYER.interrupted = False               # reset shared player
    calls = []
    monkeypatch.setattr(speech.PLAYER, "play",
                        lambda w_, interruptible=True: calls.append(w_) or True)
    out = speech.speak_stream(
        "I searched Amazon for wireless earbuds, Aditya. "
        "The lowest price I found is one thousand nine hundred ninety nine rupees. "
        "Shall I open Brave and show you the results right now?")
    assert out["ok"] is True and out["interrupted"] is False
    assert out["spoken"] == out["total"] == 3
    assert len(calls) == 3


def test_speak_stream_barge_in_stops_early(monkeypatch, tmp_path):
    """User starts talking mid-reply → playback stops at that sentence."""
    wav = tmp_path / "t.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 3200)
    monkeypatch.setattr(speech, "_synth_wav", lambda *a, **k: str(wav))
    speech.PLAYER.interrupted = False               # reset shared player
    n = {"i": 0}
    real_play = speech.PLAYER.play
    def fake_play(w_, interruptible=True):
        n["i"] += 1
        if n["i"] == 2:                     # user interrupts on sentence 2
            speech.PLAYER.interrupted = True
            return False
        return True
    monkeypatch.setattr(speech.PLAYER, "play", fake_play)
    out = speech.speak_stream(
        "First sentence is right here for you. "
        "Second sentence is a little bit longer on purpose. "
        "Third sentence arrives after the interruption happens.")
    assert out["interrupted"] is True
    assert out["spoken"] == 2 and out["total"] == 3


def test_speak_stream_empty_is_honest():
    assert speech.speak_stream("  ")["ok"] is False


# --------------------------------------------- real ffmpeg speed control
def test_atempo_speeds_up_real_wav(tmp_path):
    src = tmp_path / "s.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)          # exactly 1.0 s of silence
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    dst = tmp_path / "fast.wav"
    p = subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src),
                        "-ar", "48000", "-ac", "2",
                        "-filter:a", "atempo=1.25", str(dst)],
                       capture_output=True, timeout=60)
    assert p.returncode == 0
    with wave.open(str(dst)) as w:
        dur = w.getnframes() / w.getframerate()
    assert 0.6 < dur < 0.9                          # 1.0 s / 1.25


# ------------------------------------------- real synth latency (network)
def test_real_edge_synth_latency():
    """Measures real edge-tts latency for one sentence (network required)."""
    t0 = time.monotonic()
    wav = speech._synth_wav("Hello Aditya, this is a latency test.",
                            "en-IN-NeerjaExpressiveNeural", "neerja",
                            "-1%", "+0Hz", "warm")
    dt = time.monotonic() - t0
    if not wav:
        return                                       # offline — skip silently
    assert Path(wav).exists() and dt < 15
    print(f"\nreal edge-tts latency: {dt:.2f}s")
