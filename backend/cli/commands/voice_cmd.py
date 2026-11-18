"""Agentic voice mode — speak a command, NEURAL WEB acts on your computer.

Example: "open brave and search wireless earbuds on amazon and give the
lowest price in amazon" → the voice agent opens Brave on your desktop,
searches the web, scrapes Amazon and reports the lowest price (and can
speak the answer back via spd-say).

Pipeline:  arecord → STT (SpeechRecognition, free Google Web Speech)
           → computer-use agent (launch_app / web_query / scrape_url)
           → printed + spoken answer.

  neural-web voice                    record (Enter to stop) and act
  neural-web voice --text "..."       no mic needed — run this exact command
  neural-web voice --loop             keep listening until Ctrl+C
  neural-web voice --seconds 8        fixed-length recording
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

# default scratch workspace the voice agent operates in (auto-authorized)
VOICE_WORKSPACE = Path.home() / ".neuralweb-voice"


# ---------------------------------------------------------------- recording
def record_wav(path: str, seconds: float | None = None) -> bool:
    """Record microphone audio to 16kHz mono WAV via arecord.

    seconds=None → press Enter to stop. Returns False when no recorder
    exists or the device can't be opened.
    """
    if not _which("arecord"):
        return False
    cmd = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1", path]
    try:
        if seconds:
            cmd += ["-d", str(int(seconds))]
            p = subprocess.run(cmd, capture_output=True, timeout=seconds + 15)
            return p.returncode == 0
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        console.print("[muted]● recording… press [bold]Enter[/bold] to stop[/muted]")
        input()
        p.terminate()
        p.wait(timeout=5)
        return p.returncode in (0, None, -15, 143) and Path(path).stat().st_size > 1000
    except FileNotFoundError:
        return False
    except Exception as e:
        console.print(f"[err]recording failed: {e}[/err]")
        return False


def _which(bin_name: str) -> str:
    import shutil
    return shutil.which(bin_name) or ""


# --------------------------------------------------------------------- STT
def transcribe_wav(path: str) -> str:
    """Speech-to-text with the free Google Web Speech API (SpeechRecognition
    bundles its own flac binary — no system flac needed). Returns '' on any
    failure; never raises."""
    try:
        import speech_recognition as sr
    except ImportError:
        console.print("[err]speech_recognition not installed — "
                      "run: .venv/bin/pip install SpeechRecognition[/err]")
        return ""
    r = sr.Recognizer()
    try:
        with sr.AudioFile(path) as src:
            audio = r.record(src)
        return r.recognize_google(audio).strip()
    except sr.UnknownValueError:
        return ""
    except sr.RequestError as e:
        console.print(f"[err]STT service unavailable: {e}[/err]")
        return ""
    except Exception as e:
        console.print(f"[err]transcription failed: {e}[/err]")
        return ""


# --------------------------------------------------------------------- TTS
def speak(text: str) -> None:
    """Optional spoken reply via spd-say (silently skipped when absent)."""
    spd = _which("spd-say")
    if not spd or not text:
        return
    try:
        subprocess.Popen([spd, "-w", "-l", "en", text[:600]],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# ---------------------------------------------------------------- command
def voice(
    command: list[str] = typer.Argument(None, help="Command as text (or speak when omitted)"),
    text: bool = typer.Option(False, "--text", "-t", help="Treat the argument as text, skip the mic"),
    loop: bool = typer.Option(False, "--loop", "-l", help="Keep listening until Ctrl+C"),
    seconds: float = typer.Option(0, "--seconds", "-s", help="Fixed recording length (0 = press Enter to stop)"),
    no_tts: bool = typer.Option(False, "--no-tts", help="Don't speak the reply"),
    model: str = typer.Option("auto", "--model", "-m"),
    max_steps: int = typer.Option(12, "--max-steps"),
    workspace: str = typer.Option("", "--workspace", "-w"),
):
    """Agentic voice mode: speak a command, the agent acts on your computer."""
    from ...app.computer import permissions
    from ..agent_runner import stream_agent_run

    ws = workspace or str(VOICE_WORKSPACE)
    Path(ws).mkdir(parents=True, exist_ok=True)
    permissions.authorize_folder(ws)

    one_shot = " ".join(command or []).strip()

    while True:
        # ---- 1. get the command (mic or --text) -----------------------------
        if one_shot:
            user_cmd = one_shot      # text given on the CLI → no mic needed
        else:
            console.print(Panel("🎙  NEURAL WEB voice mode — speak your command",
                                style="bold cyan"))
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav = f.name
            if not record_wav(wav, seconds or None):
                console.print("[err]no microphone/recorder available — "
                              "use: neural-web voice --text \"...\"[/err]")
                return
            console.print("[muted]transcribing…[/muted]")
            user_cmd = transcribe_wav(wav)
            try:
                Path(wav).unlink()
            except OSError:
                pass
            if not user_cmd:
                console.print("[warn]couldn't understand the audio — try again "
                              "or use --text[/warn]")
                if not loop:
                    return
                continue

        if not user_cmd:
            return
        console.print(f'[bold]You said:[/bold] [cyan]"{user_cmd}"[/cyan]')

        # ---- 2. the agent acts on the computer ------------------------------
        result = stream_agent_run(user_cmd, ws, max_steps=max_steps,
                                  model=model, mode="computer")
        summary = (result.get("summary") or "").strip() \
            if isinstance(result, dict) else ""

        # ---- 3. reply (print + optional speech) ------------------------------
        if summary:
            console.print(Panel(summary, title="NEURAL WEB (voice reply)",
                                border_style="cyan"))
            if not no_tts:
                speak(summary)
        else:
            console.print("[warn]the agent finished without a summary[/warn]")

        one_shot = ""  # in loop mode, next iteration records the mic again
        if not loop:
            return
