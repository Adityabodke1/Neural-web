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
import select
import struct
import subprocess
import sys
import tempfile
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
        p = subprocess.run([ff, "-y", "-loglevel", "error", "-i", mp3,
                            "-ar", "24000", "-ac", "1", wav],
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


def _play_wav(wav: str) -> bool:
    for player in ("paplay", "aplay"):
        binp = _which(player)
        if binp:
            try:
                p = subprocess.run([binp, "-q", wav], capture_output=True,
                                   timeout=120)
                return p.returncode == 0
            except Exception:
                continue
    return False


def _spd_say(text: str) -> bool:
    spd = _which("spd-say")
    if not spd:
        return False
    try:
        p = subprocess.run([spd, "-w", "-l", "en", text[:600]],
                           capture_output=True, timeout=120)
        return p.returncode == 0
    except Exception:
        return False


def speak(text: str, voice_key: str = "", quiet: bool = False) -> dict:
    """Speak text with the most human voice available: Gemini native audio
    (when a Gemini key exists) else expressive edge-tts — shaped by the
    reply's mood. Never raises. Returns {'ok', 'voice', 'engine', 'mood'}."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty"}
    voice_name, key = resolve_voice(voice_key)
    rate, pitch, mood = detect_mood(text)
    style = _STYLE_BASE + f" {mood} mood: " + {
        "happy": "bright, excited, smiling through the words",
        "apologetic": "gentle, softer, a bit lower, sincere",
        "asking": "curious, playful, rising at the end",
        "serious": "calm, clear, attentive",
        "neutral": "relaxed and warm",
    }.get(mood, "relaxed and warm")
    TTS_CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        f"{key}|{rate}|{pitch}|{text[:2000]}".encode()).hexdigest()[:16]
    wav = str(TTS_CACHE / f"{digest}.wav")

    if Path(wav).exists() and Path(wav).stat().st_size > 1000:
        ok = _play_wav(wav)
        if ok:
            return {"ok": True, "voice": key, "engine": "cached",
                    "mood": mood}

    # --- best: Gemini native audio (Gemini Live quality) -------------------
    if key in ("auto-gemini",) or key.startswith("gemini-"):
        if _gemini_tts(text, wav, voice=voice_name, style=style):
            if _play_wav(wav):
                return {"ok": True, "voice": key, "engine": "gemini-tts",
                        "mood": mood}

    # --- fallback: edge-tts expressive generation ---------------------------
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "s.mp3")
        engine = ""
        edge_voice = voice_name if key in VOICES else \
            VOICES[DEFAULT_EDGE_VOICE][0]
        if _edge_tts(text, edge_voice, mp3, rate=rate, pitch=pitch):
            engine = "edge-tts"
        elif _gtts(text, os.path.join(td, "g.mp3")):
            mp3 = os.path.join(td, "g.mp3")
            engine = "gtts"
        if engine and _convert(mp3, wav):
            if _play_wav(wav):
                return {"ok": True, "voice": key, "engine": engine,
                        "mood": mood}
        if _spd_say(text):
            return {"ok": True, "voice": key, "mood": mood,
                    "engine": "spd-say (robotic fallback)"}
    return {"ok": False, "voice": key, "mood": mood,
            "reason": "no TTS engine worked"}


def _which(name: str) -> str:
    import shutil
    return shutil.which(name) or ""
