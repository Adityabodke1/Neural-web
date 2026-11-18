"""Voice I/O for NEURAL WEB: recording, STT and natural TTS.

STT  — arecord (with VAD auto-stop) → silence trim + normalization
       → free Google Web Speech (SpeechRecognition bundles its own flac).
TTS  — Microsoft Edge NEURAL voices (free, no key): real human-sounding
       girl voices, e.g. en-IN-NeerjaNeural. mp3 → wav via imageio-ffmpeg's
       bundled ffmpeg → aplay. Fallbacks: gTTS → spd-say. Never raises.

Voice preference persists in ~/.config/neuralweb/voice.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

# ---------------------------------------------------------------- config
PREFS_PATH = Path.home() / ".config" / "neuralweb" / "voice.json"
TTS_CACHE = Path.home() / ".cache" / "neuralweb" / "tts"
RATE = 16000                    # recording sample rate (mono s16)

# preset name → (edge-tts voice, description)
VOICES: dict[str, tuple[str, str]] = {
    "neerja": ("en-IN-NeerjaExpressiveNeural",
               "Indian English girl, expressive generation (default)"),
    "ava": ("en-US-AvaMultilingualNeural", "US girl, new multilingual gen"),
    "emma": ("en-US-EmmaMultilingualNeural", "US girl, warm & natural"),
    "sonia": ("en-GB-SoniaNeural", "British English girl"),
    "swara": ("hi-IN-SwaraNeural", "Hindi girl"),
}
DEFAULT_VOICE = "auto"
DEFAULT_LANGUAGE = "en-IN"      # matches Indian accents far better than en-US

# ---------------------------------------------------- Gemini native TTS
# Same native-audio models as Gemini Live — by far the most human result.
# Activated automatically whenever a Gemini key exists (neural-web api add gemini).
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_VOICES: dict[str, str] = {
    "leda": "Leda",        # youthful, lively girl (default)
    "kore": "Kore",        # firm, confident
    "zephyr": "Zephyr",    # bright
    "aoede": "Aoede",      # breezy, easy-going
}
_STYLE_BASE = ("Read this warmly and naturally, like a cheerful close friend "
               "chatting with her favourite human — relaxed, real, with a "
               "hint of playful flirt. Not a narrator, not an assistant.")


def _gemini_key() -> str:
    try:
        from ..providers.secrets_store import get_key
        return get_key("gemini") or ""
    except Exception:
        return ""

# ------------------------------------------------------------ mood/emotion
# The free edge-tts endpoint takes plain text (no SSML styles), but the
# EXPRESSIVE voice generation + per-mood rate/pitch shaping gives clearly
# different delivery: happy lines are bouncier, apologies are softer.
_MOOD_RULES: list[tuple[str, str, str, str]] = [
    # (regex on lowercase text, rate, pitch, label)
    (r"\b(great|awesome|amazing|found it|excellent|perfect|congratulations|"
     r"successfully|good news|love)\b|!", "-3%", "+3Hz", "happy"),
    (r"\b(sorry|unfortunately|apologize|couldn'?t|could not|unable|failed|"
     r"my apologies|afraid)\b", "-6%", "-2Hz", "apologetic"),
    (r"\?\s*$|\b(should i|shall i|want me|shall we|would you like)\b",
     "-1%", "+2Hz", "asking"),
    (r"\b(important|careful|warning|note that|be aware)\b", "-4%", "+1Hz",
     "serious"),
]


def detect_mood(text: str) -> tuple[str, str, str]:
    """(rate, pitch, mood label) for a reply — gives the voice real emotion.
    Shaping is deliberately gentle: heavy pitch shifts sound synthetic."""
    t = (text or "").lower()
    for pat, rate, pitch, label in _MOOD_RULES:
        import re
        if re.search(pat, t):
            return rate, pitch, label
    return "-1%", "+0Hz", "neutral"      # natural conversational default


def load_prefs() -> dict:
    try:
        return json.loads(PREFS_PATH.read_text())
    except Exception:
        return {}


def save_prefs(prefs: dict) -> None:
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        PREFS_PATH.write_text(json.dumps(prefs, indent=1))
    except Exception:
        pass


def _edge_default_for_lang() -> str:
    """Edge voice that matches the saved STT language (hi → Swara)."""
    lang = (load_prefs().get("language") or DEFAULT_LANGUAGE)
    return "swara" if lang.lower().startswith("hi") else DEFAULT_EDGE_VOICE


def resolve_voice(voice_key: str = "") -> tuple[str, str]:
    """(engine voice name, preset key). 'auto' picks the best available:
    Gemini native voice when a Gemini key exists, else the expressive
    edge-tts voice matching your language. Falls back to saved/default."""
    prefs = load_prefs()
    key = (voice_key or prefs.get("voice") or DEFAULT_VOICE).strip().lower()
    if key == "auto":
        if _gemini_key():
            return GEMINI_VOICES["leda"], "auto-gemini"
        edge_key = _edge_default_for_lang()
        return VOICES[edge_key][0], f"auto-edge"
    if key == "auto-edge":
        edge_key = _edge_default_for_lang()
        return VOICES[edge_key][0], "auto-edge"
    if key in GEMINI_VOICES:                      # explicit gemini preset
        return GEMINI_VOICES[key], f"gemini-{key}"
    if key.startswith("gemini-"):                 # gemini-leda style keys
        sub = key.split("-", 1)[1]
        if sub in GEMINI_VOICES:
            return GEMINI_VOICES[sub], key
    if key not in VOICES:
        for name, (v, _) in VOICES.items():       # allow full voice ids too
            if key and key.lower() in v.lower():
                return v, name
        key = prefs.get("voice") or DEFAULT_VOICE
        if key in ("auto", "auto-gemini", "auto-edge") and key not in VOICES:
            key = _edge_default_for_lang()
        if key not in VOICES:
            key = DEFAULT_EDGE_VOICE
    return VOICES[key][0], key


DEFAULT_EDGE_VOICE = "neerja"


# ---------------------------------------------------------------- recording
def _windows_rms(pcm: bytes, rate: int, window_ms: int = 10) -> list[float]:
    n = max(1, int(rate * window_ms / 1000))
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm[: len(pcm) // 2 * 2])
    return [ (sum(v * v for v in samples[i:i + n]) / n) ** 0.5
             for i in range(0, len(samples), n) ]


def trim_and_boost(pcm: bytes, rate: int = RATE) -> bytes:
    """Cut leading/trailing silence (Google STT fails on silence-padded
    clips) and normalize loudness. Pure stdlib; never raises."""
    try:
        if len(pcm) < int(rate * 0.2) * 2:
            return pcm
        samples = list(struct.unpack("<%dh" % (len(pcm) // 2),
                                     pcm[: len(pcm) // 2 * 2]))
        # DC offset
        mean = sum(samples) / len(samples)
        samples = [s - mean for s in samples]
        window = int(rate * 0.01)
        rms = [ (sum(v * v for v in samples[i:i + window]) / window) ** 0.5
                for i in range(0, len(samples), window) ]
        peak = max(rms) or 1.0
        thresh = max(180.0, peak * 0.06)
        idx = [i for i, r in enumerate(rms) if r > thresh]
        if not idx:
            return b""
        pad = window * 15                       # 150 ms padding
        a = max(0, idx[0] * window - pad)
        b = min(len(samples), (idx[-1] + 1) * window + pad)
        seg = samples[a:b]
        # normalize to ~75% of full scale (gentle cap so it never sounds harsh)
        seg_peak = max(abs(s) for s in seg) or 1
        gain = min(12.0, 24000.0 / seg_peak)
        if gain > 1.01:
            seg = [max(-32767, min(32767, int(s * gain))) for s in seg]
        return struct.pack("<%dh" % len(seg), *seg)
    except Exception:
        return pcm


def record_command(seconds: float = 0, device: str = "",
                   max_seconds: float = 30.0) -> str:
    """Record a voice command and return the processed wav path ('' on fail).

    seconds=0 and a TTY  → press Enter to start; recording AUTO-STOPS ~1.5 s
    after you stop speaking (VAD), or press Enter again to stop early.
    seconds=N            → fixed-length recording (headless-safe).
    """
    if not _which("arecord"):
        return ""
    args = ["arecord", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"]
    if device:
        args += ["-D", device]
    try:
        p = subprocess.Popen(args, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
    except Exception:
        return ""

    fixed = seconds > 0 or not sys.stdin.isatty()
    if not fixed:
        print("● listening… speak now (stops automatically when you finish, "
              "or press Enter)")
    buf = bytearray()
    speech_started = False
    silence_ms = 0
    loud_every = 0          # debug-free level tracking
    started = time.monotonic()
    chunk_bytes = RATE // 10 * 2            # 0.1 s of s16 mono

    while p.poll() is None:
        if time.monotonic() - started > max_seconds:
            break
        streams = [p.stdout]
        if not fixed:
            streams.append(sys.stdin)
        r, _, _ = select.select(streams, [], [], 0.1)
        if not fixed and sys.stdin in r:
            sys.stdin.readline()            # user pressed Enter → stop
            break
        if p.stdout in r:
            chunk = p.stdout.read(chunk_bytes) or b""
            if not chunk:
                break
            buf += chunk
            rms = (_windows_rms(chunk, RATE, 100) or [0.0])[0]
            loud_every += 1
            if rms > 900:                   # ~4% of full scale
                speech_started = True
                silence_ms = 0
            elif speech_started:
                silence_ms += 100
                if silence_ms >= 1500:      # 1.5 s of quiet after speech
                    break
            if fixed and seconds > 0 and \
                    time.monotonic() - started >= seconds:
                break
    try:
        p.terminate()
        p.wait(timeout=3)
    except Exception:
        pass

    pcm = trim_and_boost(bytes(buf), RATE)
    if len(pcm) < RATE:                     # < 1 s of actual speech-ish audio
        return ""
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    with wave.open(out.name, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.name


# --------------------------------------------------------------------- STT
def transcribe_wav(path: str, language: str = "") -> str:
    """Speech-to-text with the free Google Web Speech API. Returns '' on any
    failure; never raises. language defaults to the saved/en-IN preference."""
    try:
        import speech_recognition as sr
    except ImportError:
        return ""
    lang = language or load_prefs().get("language") or DEFAULT_LANGUAGE
    r = sr.Recognizer()
    try:
        with sr.AudioFile(path) as src:
            audio = r.record(src)
        return r.recognize_google(audio, language=lang).strip()
    except Exception:
        return ""


# --------------------------------------------------------------------- TTS
def _gemini_tts(text: str, wav_out: str, voice: str = "Leda",
                style: str = "") -> bool:
    """Gemini native audio TTS (Gemini Live family) → 24 kHz PCM → wav.
    Returns False when no key or the call fails (fallback continues)."""
    key = _gemini_key()
    if not key:
        return False
    try:
        import base64
        import urllib.request
        body = {
            "contents": [{"parts": [{"text": f"{style}\n\n{text}"}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig":
                                                 {"voiceName": voice}}},
            },
        }
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_TTS_MODEL}:generateContent",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
        pcm = base64.b64decode(
            data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
        with wave.open(wav_out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        return os.path.getsize(wav_out) > 1000
    except Exception:
        return False


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return _which("ffmpeg")


def _convert(mp3: str, wav: str) -> bool:
    ff = _ffmpeg()
    if not ff:
        return False
    try:
        # resample to 48 kHz stereo — many HDA sound chips sound flat and
        # metallic playing 24 kHz mono directly; this removes that artifact
        p = subprocess.run([ff, "-y", "-loglevel", "error", "-i", mp3,
                            "-ar", "48000", "-ac", "2", wav],
                           capture_output=True, timeout=60)
        return p.returncode == 0 and os.path.getsize(wav) > 1000
    except Exception:
        return False


def _edge_tts(text: str, edge_voice: str, mp3: str,
              rate: str = "-2%", pitch: str = "+2Hz") -> bool:
    try:
        import asyncio
        import edge_tts
        async def run():
            c = edge_tts.Communicate(text, edge_voice, rate=rate, pitch=pitch)
            await c.save(mp3)
        asyncio.run(asyncio.wait_for(run(), timeout=45))
        return os.path.getsize(mp3) > 1000
    except Exception:
        return False


def _gtts(text: str, mp3: str, lang_hint: str = "en") -> bool:
    try:
        from gtts import gTTS
        gTTS(text=text, lang=lang_hint[:2]).save(mp3)
        return os.path.getsize(mp3) > 1000
    except Exception:
        return False


# ---------------------------------------------------------------- player
class _Player:
    """Owns THE one active playback process: no overlapping audio, and
    barge-in (user starts talking) stops it immediately."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.interrupted = False

    def stop(self) -> None:
        with self._lock:
            self.interrupted = True
            p, self._proc = self._proc, None
            if p is not None and p.poll() is None:
                try:
                    p.terminate()
                    p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass

    def play(self, wav: str, timeout: float = 300,
             interruptible: bool = True) -> bool:
        """Play a wav. Returns False if stopped/interrupted/failed."""
        binp = _which("paplay") or _which("aplay")
        if not binp or not Path(wav).exists():
            return False
        with self._lock:
            # never two voices at once — cut the previous one instantly
            if self._proc is not None and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
            self.interrupted = False
            self._proc = subprocess.Popen(
                [binp, "-q", wav], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        if interruptible:
            mon = threading.Thread(target=_barge_in_watch,
                                   args=(self,), daemon=True)
            mon.start()
        try:
            self._proc.wait(timeout=timeout)
        except Exception:
            self.stop()
            return False
        return not self.interrupted


PLAYER = _Player()


def stop_speaking() -> None:
    """Interrupt whatever she is saying right now."""
    PLAYER.stop()


def _barge_in_watch(player: "_Player", chunk_s: float = 0.1,
                    threshold: float = 29500.0, loud_chunks: int = 3) -> None:
    """Watch the mic (+ keyboard) while she speaks; real interruption =
    the user talking OVER her. Calibration note: on this machine the mic
    picks up her own playback at RMS ~21k (speaker bleed) and room noise
    sits at ~7.5k — so the bar is set ABOVE the bleed ceiling and requires
    ~300 ms of sustained near-clipping speech, plus Enter as a manual
    escape hatch. Fails silently when no mic/tty — barge-in is a bonus."""
    import select as _select
    if not _which("arecord"):
        return
    try:
        p = subprocess.Popen(
            ["arecord", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception:
        return
    loud = 0
    try:
        while player._proc is not None and player._proc.poll() is None:
            streams = [p.stdout]
            if sys.stdin.isatty():
                streams.append(sys.stdin)
            r, _, _ = _select.select(streams, [], [], chunk_s)
            if sys.stdin in r:
                sys.stdin.readline()       # manual interrupt (Enter)
                player.stop()
                break
            if p.stdout in r:
                chunk = p.stdout.read(int(RATE * chunk_s) * 2) or b""
                if not chunk:
                    break
                vals = struct.unpack("<%dh" % (len(chunk) // 2),
                                     chunk[: len(chunk) // 2 * 2])
                rms = (sum(v * v for v in vals) / max(1, len(vals))) ** 0.5
                if rms > threshold:
                    loud += 1
                    if loud >= loud_chunks:
                        player.stop()      # user spoke over her → cut now
                        break
                else:
                    loud = 0
    except Exception:
        pass
    finally:
        try:
            p.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------- streaming
_SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+")


def split_sentences(text: str, min_len: int = 22) -> list[str]:
    """Split a reply into speakable sentences (handles Hindi '।' too).
    Very short fragments are merged back so there are no awkward gaps."""
    parts = [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]
    out: list[str] = []
    for s in parts:
        if out and len(s) < min_len:
            out[-1] += " " + s
        else:
            out.append(s)
    return out or ([text.strip()] if (text or "").strip() else [])


def _user_rate_pct() -> int:
    """User speaking-speed preference, e.g. '+10%' → 10.
    Default is slightly faster than stock TTS (+6%) — a small touch of
    pace reads as natural conversation without feeling rushed."""
    raw = str(load_prefs().get("rate") or "").strip()
    m = re.match(r"^([+-]?\d+)\s*%$", raw)
    if m:
        return int(m.group(1))
    return 6


def _combine_rate(mood_rate: str) -> str:
    total = int(mood_rate.replace("%", "").replace("+", "") or 0) + _user_rate_pct()
    total = max(-50, min(80, total))
    return f"{total:+d}%"

def _atempo_factor() -> float:
    return max(0.5, min(2.0, 1.0 + _user_rate_pct() / 100.0))


def _synth_wav(sentence: str, engine_voice: str, engine_key: str,
               mood_rate: str, mood_pitch: str, style: str) -> str:
    """Synthesize ONE sentence to a cached wav ('' on failure).
    Returns a ready-to-play 48 kHz stereo file for every engine."""
    TTS_CACHE.mkdir(parents=True, exist_ok=True)
    rate = _combine_rate(mood_rate)
    digest = hashlib.sha1(
        f"{engine_key}|{engine_voice}|{rate}|{mood_pitch}|{sentence}".encode()
    ).hexdigest()[:16]
    wav = str(TTS_CACHE / f"{digest}.wav")
    if Path(wav).exists() and Path(wav).stat().st_size > 1000:
        return wav
    atempo = _atempo_factor()
    atempo_arg = ["-filter:a", f"atempo={atempo}"] if abs(atempo - 1.0) > 0.02 else []

    # 1) Gemini native audio (Gemini Live family) — when a key exists
    if engine_key in ("auto-gemini",) or engine_key.startswith("gemini-"):
        pcm_wav = wav + ".pcm.wav"
        if _gemini_tts(sentence, pcm_wav, voice=engine_voice, style=style):
            if not atempo_arg:
                return pcm_wav            # 24k mono plays fine via aplay
            try:
                ff = _ffmpeg()
                p = subprocess.run(
                    [ff, "-y", "-loglevel", "error", "-i", pcm_wav,
                     "-ar", "48000", "-ac", "2", *atempo_arg, wav],
                    capture_output=True, timeout=60)
                if p.returncode == 0 and os.path.getsize(wav) > 1000:
                    return wav
            except Exception:
                pass
            return pcm_wav

    # 2) edge-tts neural voices (free, no key)
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "s.mp3")
        used = ""
        edge_voice = engine_voice if engine_key in VOICES else \
            VOICES[DEFAULT_EDGE_VOICE][0]
        if _edge_tts(sentence, edge_voice, mp3, rate=rate, pitch=mood_pitch):
            used = "edge"
        elif _gtts(sentence, os.path.join(td, "g.mp3")):
            mp3 = os.path.join(td, "g.mp3")
            used = "gtts"
        if used:
            try:
                ff = _ffmpeg()
                p = subprocess.run(
                    [ff, "-y", "-loglevel", "error", "-i", mp3,
                     "-ar", "48000", "-ac", "2", *atempo_arg, wav],
                    capture_output=True, timeout=60)
                if p.returncode == 0 and os.path.getsize(wav) > 1000:
                    return wav
            except Exception:
                pass
    return ""


_MOOD_STYLE = {
    "happy": "bright, excited, smiling through the words",
    "apologetic": "gentle, softer, a bit lower, sincere",
    "asking": "curious, playful, rising at the end",
    "serious": "calm, clear, attentive",
    "neutral": "relaxed and warm",
}


def _jitter(index: int, sentence: str) -> tuple[int, int]:
    """Deterministic per-sentence prosody variation (rate %, pitch Hz).
    Stock TTS delivers every sentence at identical pace/pitch — the
    metronome effect that screams 'robot'. Tiny seeded variation makes
    consecutive sentences feel like one flowing human thought."""
    h = int(hashlib.sha1(f"{index}|{sentence}".encode()).hexdigest()[:6], 16)
    return (h % 5) - 2, (h % 3) - 1          # rate -2..+2 %, pitch -1..+1 Hz


def _shift_rate(rate: str, delta_pct: int) -> str:
    try:
        v = int(rate.replace("%", "").replace("+", "") or 0) + delta_pct
        return f"{max(-50, min(80, v)):+d}%"
    except Exception:
        return rate


def _shift_pitch(pitch: str, delta_hz: int) -> str:
    try:
        v = int(pitch.replace("Hz", "").replace("+", "") or 0) + delta_hz
        return f"{v:+d}Hz"
    except Exception:
        return pitch


def speak_stream(text: str, voice_key: str = "") -> dict:
    """Speak a reply sentence-by-sentence: synthesis of the NEXT sentence
    happens while the CURRENT one plays, so the first words are audible in
    ~1 s instead of after the whole reply. The user can interrupt (barge-in)
    at any moment; playback then stops instantly. Never raises."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}
    voice_name, key = resolve_voice(voice_key)
    rate, pitch, mood = detect_mood(text)
    style = _STYLE_BASE + f" {mood} mood: " + \
        _MOOD_STYLE.get(mood, "relaxed and warm")
    sents = split_sentences(text)
    if not sents:
        return {"ok": False, "reason": "empty"}

    engine_label = ("gemini-tts" if (key == "auto-gemini"
                                     or key.startswith("gemini-"))
                    and _gemini_key() else "edge-tts")
    t0 = time.monotonic()
    from concurrent.futures import ThreadPoolExecutor
    spoken = 0
    first_audio_s = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        dr, dp = _jitter(0, sents[0])
        fut = pool.submit(_synth_wav, sents[0], voice_name, key,
                          _shift_rate(rate, dr), _shift_pitch(pitch, dp),
                          style)
        for i in range(len(sents)):
            wav = fut.result()
            if i + 1 < len(sents):
                dr2, dp2 = _jitter(i + 1, sents[i + 1])
                fut = pool.submit(_synth_wav, sents[i + 1], voice_name,
                                  key, _shift_rate(rate, dr2),
                                  _shift_pitch(pitch, dp2), style)
            if not wav:
                continue
            if first_audio_s is None:
                first_audio_s = round(time.monotonic() - t0, 2)
            PLAYER.play(wav, interruptible=True)
            spoken += 1
            if PLAYER.interrupted:
                return {"ok": True, "voice": key, "engine": engine_label,
                        "mood": mood, "interrupted": True,
                        "spoken": spoken, "total": len(sents),
                        "first_audio_s": first_audio_s}
    ok = spoken > 0 or _spd_say(text)
    return {"ok": ok, "voice": key, "engine": engine_label if spoken else
            ("spd-say (robotic fallback)" if spoken == 0 and ok else engine_label),
            "mood": mood, "interrupted": False, "spoken": spoken,
            "total": len(sents), "first_audio_s": first_audio_s,
            "hint": _quality_hint(key)}


def _quality_hint(key: str) -> str:
    """One-line nudge when she is NOT on the best voice."""
    if _gemini_key():
        return ""
    return ("voice tip: this is the free Edge fallback — for the Gemini "
            "Live-quality voice run: neural-web api add gemini")


def speak(text: str, voice_key: str = "", quiet: bool = False) -> dict:
    """Backwards-compatible entry point — now streaming + interruptible."""
    return speak_stream(text, voice_key)


def _which(name: str) -> str:
    import shutil
    return shutil.which(name) or ""
