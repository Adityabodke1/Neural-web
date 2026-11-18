"""Agentic voice mode — speak a command, NEURAL WEB acts on your computer.

Example: "open brave and search wireless earbuds on amazon and give the
lowest price in amazon" → the voice agent opens Brave on your desktop,
searches the web, scrapes Amazon and reports the lowest price, spoken back
in a natural, expressive girl voice (Microsoft Edge neural voices — free).

  neural-web voice                     listen → speak (auto-stops when you finish)
  neural-web voice --text "..."        no mic needed — run this exact command
  neural-web voice --loop              continuous listen-act loop
  neural-web voice --list-voices       hear all girl voices and pick one
  neural-web voice --voice emma        switch voice (saved for next runs)
"""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

from ...app.voice import speech          # noqa: E402  (needs package context)

# default scratch workspace the voice agent operates in (auto-authorized)
VOICE_WORKSPACE = Path.home() / ".neuralweb-voice"


# ---------------------------------------------------------------- command
def voice(
    command: list[str] = typer.Argument(None, help="Command as text (or speak when omitted)"),
    text: bool = typer.Option(False, "--text", "-t", help="Treat the argument as text, skip the mic"),
    loop: bool = typer.Option(False, "--loop", "-l", help="Keep listening until Ctrl+C"),
    seconds: float = typer.Option(0, "--seconds", "-s", help="Fixed recording length (0 = auto-stop after speech)"),
    no_tts: bool = typer.Option(False, "--no-tts", help="Don't speak the reply"),
    voice_name: str = typer.Option("", "--voice", "-v", help="Voice preset: neerja, ava, emma, sonia, swara"),
    language: str = typer.Option("", "--language", help="STT language (e.g. en-IN, en-US, hi-IN)"),
    device: str = typer.Option("", "--device", "-d", help="ALSA capture device (default: auto)"),
    max_steps: int = typer.Option(12, "--max-steps"),
    model: str = typer.Option("auto", "--model", "-m"),
    workspace: str = typer.Option("", "--workspace", "-w"),
    list_voices: bool = typer.Option(False, "--list-voices", help="List available girl voices"),
):
    """Agentic voice mode: speak a command, the agent acts on your computer."""
    from ...app.computer import permissions
    from ..agent_runner import stream_agent_run

    ws = workspace or str(VOICE_WORKSPACE)
    Path(ws).mkdir(parents=True, exist_ok=True)
    permissions.authorize_folder(ws)

    if list_voices:
        _pick_voice(voice_name)
        return
    if voice_name:
        prefs = speech.load_prefs()
        prefs["voice"] = speech.resolve_voice(voice_name)[1]
        speech.save_prefs(prefs)
        console.print(f"[ok]voice saved: [bold cyan]{prefs['voice']}[/bold cyan] "
                      f"({speech.VOICES[prefs['voice']][0]})[/ok]")
    if language:
        prefs = speech.load_prefs()
        prefs["language"] = language
        speech.save_prefs(prefs)
        console.print(f"[ok]STT language saved: {language}[/ok]")

    one_shot = " ".join(command or []).strip()

    while True:
        # ---- 1. get the command (mic or --text) -----------------------------
        if one_shot:
            user_cmd = one_shot      # text given on the CLI → no mic needed
        else:
            console.print(Panel("🎙  NEURAL WEB voice mode — speak your command",
                                style="bold cyan"))
            if seconds <= 0 and not sys.stdin.isatty():
                rec_seconds = 6.0    # piped stdin: fixed length
            else:
                rec_seconds = seconds
            wav = speech.record_command(seconds=rec_seconds, device=device)
            if not wav:
                console.print("[err]no clear speech captured — try again "
                              "(speak a bit louder) or use: "
                              'neural-web voice --text "..."[/err]')
                if not loop:
                    return
                continue
            console.print("[muted]transcribing…[/muted]")
            user_cmd = speech.transcribe_wav(wav, language=language)
            try:
                Path(wav).unlink()
            except OSError:
                pass
            if not user_cmd:
                console.print("[warn]couldn't understand that — try again, "
                              "speak clearly, or use --text[/warn]")
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

        # ---- 3. reply (print + spoken, expressive voice) ---------------------
        if summary:
            console.print(Panel(summary, title="NEURAL WEB (voice reply)",
                                border_style="cyan"))
            if not no_tts:
                out = speech.speak(summary)
                if out.get("ok"):
                    console.print(f'[muted]🔊 {out["engine"]} · voice '
                                  f'{out["voice"]} · mood {out.get("mood", "neutral")}[/muted]')
        else:
            console.print("[warn]the agent finished without a summary[/warn]")

        one_shot = ""  # in loop mode, next iteration listens again
        if not loop:
            return


def _pick_voice(current: str = "") -> None:
    """--list-voices: print presets, optionally preview + switch."""
    prefs = speech.load_prefs()
    active = prefs.get("voice") or speech.DEFAULT_VOICE
    for i, (key, (edge_name, desc)) in enumerate(speech.VOICES.items(), 1):
        mark = "✔" if key == active else "·"
        console.print(f"  [{mark}] [cyan]{i}. {key}[/cyan] — {desc} "
                      f"[muted]({edge_name})[/muted]")
    if not current:
        console.print("[muted]switch: neural-web voice --voice <name> "
                      "(saved) · preview: --list-voices <name>[/muted]")
        return
    key = speech.resolve_voice(current)[1]
    console.print(f"[muted]▶ previewing [bold]{key}[/bold] — "
                  f"'Hi! I'm your NEURAL WEB assistant.'[/muted]")
    out = speech.speak("Hi! I am your NEURAL WEB assistant, and I am really "
                       "happy to help you today.", voice_key=key)
    console.print(f'[muted]{"✔ played" if out.get("ok") else "✖ playback failed"} '
                  f"({out.get('engine', out.get('reason'))})[/muted]")
