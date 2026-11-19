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
# exit words: user leaves voice mode manually when done
_EXIT_RE = None


def is_exit_phrase(text: str) -> bool:
    """'bye' (or goodnight/exit variants) ends the voice conversation."""
    global _EXIT_RE
    import re
    if _EXIT_RE is None:
        _EXIT_RE = re.compile(
            r"^\s*(bye|bye bye|goodbye|good bye|good night|goodnight|see you|"
            r"see ya|alvida|chalo bye|exit|quit|close voice mode|band karo|"
            r"बंद करो|बाय|अलविदा|अब छोड़ो|so jao|शुभ रात्रि)[\s!.…]*$",
            re.I)
    return bool(_EXIT_RE.match(text or ""))


def voice(
    command: list[str] = typer.Argument(None, help="Command as text (or speak when omitted)"),
    text: bool = typer.Option(False, "--text", "-t", help="Treat the argument as text, skip the mic"),
    loop: bool = typer.Option(True, "--loop/--once", "-l", help="Conversation mode: keep listening until you say 'bye' (default)"),
    seconds: float = typer.Option(0, "--seconds", "-s", help="Fixed recording length (0 = auto-stop after speech)"),
    no_tts: bool = typer.Option(False, "--no-tts", help="Don't speak the reply"),
    voice_name: str = typer.Option("", "--voice", "-v", help="Voice preset: auto, gemini-leda, neerja, ava, emma, sonia, swara"),
    rate: str = typer.Option("", "--rate", help="Speaking speed, e.g. '+10%%' faster or '-10%%' slower (saved)"),
    language: str = typer.Option("", "--language", help="STT language (e.g. en-IN, en-US, hi-IN)"),
    device: str = typer.Option("", "--device", "-d", help="ALSA capture device (default: auto)"),
    hindi: bool = typer.Option(False, "--hindi", help="Hindi mode: Hindi STT + Hindi voice + she replies in Hindi"),
    max_steps: int = typer.Option(12, "--max-steps"),
    model: str = typer.Option("auto", "--model", "-m"),
    workspace: str = typer.Option("", "--workspace", "-w"),
    list_voices: bool = typer.Option(False, "--list-voices", help="List available girl voices"),
):
    """Agentic voice mode: speak a command, she acts on your computer.

    She keeps listening after every reply until you say 'bye' (or Ctrl+C)."""
    from ...app.computer import permissions
    from ...app.voice import companion
    from ..agent_runner import stream_agent_run

    ws = workspace or str(VOICE_WORKSPACE)
    Path(ws).mkdir(parents=True, exist_ok=True)
    permissions.authorize_folder(ws)

    if list_voices:
        _pick_voice(voice_name)
        return
    if voice_name:
        vn = voice_name.strip().lower()
        prefs = speech.load_prefs()
        prefs["voice"] = vn if vn == "auto" else speech.resolve_voice(vn)[1]
        speech.save_prefs(prefs)
        console.print(f"[ok]voice saved: [bold cyan]{prefs['voice']}[/bold cyan][/ok]")
    if rate:
        import re as _re
        if not _re.match(r"^[+-]?\d+\s*%$", rate.strip()):
            console.print("[err]--rate looks like '+10%' or '-15%'[/err]")
            return
        prefs = speech.load_prefs()
        prefs["rate"] = rate.strip()
        speech.save_prefs(prefs)
        console.print(f"[ok]speaking speed saved: {rate.strip()}[/ok]")
    if language:
        prefs = speech.load_prefs()
        prefs["language"] = language
        speech.save_prefs(prefs)
        console.print(f"[ok]STT language saved: {language}[/ok]")
    if hindi:
        prefs = speech.load_prefs()
        prefs["language"] = "hi-IN"
        prefs["voice"] = "auto"          # → Swara (Hindi girl) / Gemini Hindi
        speech.save_prefs(prefs)
        language = "hi-IN"
        console.print("[ok]हिंदी mode on — बोलो, मैं सुन रही हूँ (Hindi STT + "
                      "Hindi voice, saved)[/ok]")

    one_shot = " ".join(command or []).strip()
    # one-shot when: --once, or --text given (headless/testing), else she
    # converses until 'bye'
    conversational = loop and not (one_shot and text)
    persona = ""
    if conversational:
        persona = companion.persona_prompt()
        if not speech._gemini_key():
            console.print(Panel(
                "This is the FREE fallback voice. For the [bold]fully human, "
                "Gemini Live-level voice[/bold] (real emotion, natural "
                "breathing, Hindi + English):\n\n"
                "  1. Get a free key: [cyan]https://aistudio.google.com/apikey[/cyan]\n"
                "  2. Run: [cyan]neural-web api add gemini[/cyan]\n\n"
                "It takes 30 seconds — she upgrades automatically, forever.",
                title="🎙 voice upgrade available", border_style="yellow"))
        console.print('[muted]💬 conversation mode — say "bye" when you\'re '
                      "done (Ctrl+C also works)[/muted]")

    while True:
        # ---- 1. get the command (mic or --text) -----------------------------
        if one_shot:
            user_cmd = one_shot      # text given on the CLI → no mic needed
        else:
            console.print(Panel("🎙  listening… (say 'bye' to leave)",
                                style="bold cyan"))
            if seconds <= 0 and not sys.stdin.isatty():
                rec_seconds = 6.0    # piped stdin: fixed length
            else:
                rec_seconds = seconds
            try:
                wav = speech.record_command(seconds=rec_seconds, device=device)
            except (KeyboardInterrupt, EOFError):
                console.print("\n[cyan]👋 voice mode closed[/cyan]")
                return
            if not wav:
                console.print("[err]no clear speech captured — try again "
                              "(speak a bit louder) or use: "
                              'neural-web voice --text "..."[/err]')
                if not conversational:
                    return
                continue
            console.print("[muted]transcribing…[/muted]")
            user_cmd = speech.transcribe_wav(wav, language=language)
            if not user_cmd:
                # fallback: try the other language before giving up
                saved = (language or speech.load_prefs().get("language") or "en-IN")
                alt = "en-IN" if saved.startswith("hi") else "hi-IN"
                user_cmd = speech.transcribe_wav(wav, language=alt)
            try:
                Path(wav).unlink()
            except OSError:
                pass
            if not user_cmd:
                console.print("[warn]couldn't understand that — listening "
                              "again… (or Ctrl+C to leave)[/warn]")
                if not conversational:
                    return
                continue

        if not user_cmd:
            return

        # ---- 2. user is done → leave voice mode -----------------------------
        if conversational and is_exit_phrase(user_cmd):
            console.print("[muted]you said bye — leaving voice mode[/muted]")
            if not no_tts:
                if (language or speech.load_prefs().get("language") or
                        "").startswith("hi"):
                    speech.speak("ठीक है बाय! तुमसे बात करके मज़ा आया। "
                                 "फिर बुलाना, मैं यहीं हूँ।")
                else:
                    speech.speak("Okay bye! I had fun with you. Call me "
                                 "anytime, I'll be right here.")
            console.print("[cyan]👋 voice mode closed[/cyan]")
            return

        console.print(f'[bold]You said:[/bold] [cyan]"{user_cmd}"[/cyan]')

        # ---- 3. the agent acts on the computer ------------------------------
        try:
            result = stream_agent_run(user_cmd, ws, max_steps=max_steps,
                                      model=model, mode="computer",
                                      persona=persona)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[muted]task interrupted — she'll be here[/muted]")
            return
        summary = (result.get("summary") or "").strip() \
            if isinstance(result, dict) else ""

        # ---- 4. reply (streamed audio, interruptible — barge in anytime) ----
        if summary:
            console.print(Panel(summary, title="NEURAL WEB (voice reply)",
                                border_style="cyan"))
            if not no_tts:
                out = speech.speak(summary)
                if out.get("ok"):
                    extra = f' · mood {out.get("mood", "neutral")}'
                    if out.get("interrupted"):
                        extra += f' · interrupted at {out.get("spoken")}/{out.get("total")} sentences'
                    console.print(f'[muted]🔊 {out["engine"]} · voice '
                                  f'{out["voice"]}{extra} '
                                  f'· first audio {out.get("first_audio_s", "?")}s[/muted]')
                    if out.get("hint") and not getattr(voice, "_hint_shown", False):
                        voice._hint_shown = True
                        console.print(f"[yellow]{out['hint']}[/yellow]")
                if out.get("interrupted"):
                    console.print("[muted]you spoke — she stopped, listening…[/muted]")
        else:
            console.print("[warn]the agent finished without a summary[/warn]")

        # ---- 5. she remembers this (background, never blocks) ---------------
        from ...app import conversations as conv
        conv.log_turn("user", user_cmd, session="voice", workspace=ws or "")
        if summary:
            conv.log_turn("assistant", summary, session="voice",
                          workspace=ws or "")
        if conversational:
            companion.log_interaction(user_cmd, summary)
            companion.extract_in_background(user_cmd, summary)

        one_shot = ""  # text mode runs once; conversation keeps listening
        if not conversational:
            return


def _pick_voice(current: str = "") -> None:
    """--list-voices: print presets, optionally preview + switch."""
    prefs = speech.load_prefs()
    active = prefs.get("voice") or speech.DEFAULT_VOICE
    has_gem = bool(speech._gemini_key())
    console.print(f"  [{'✔' if active == 'auto' else '·'}] [cyan]0. auto[/cyan]"
                  " — best available: Gemini Live-style voice when a Gemini "
                  f"key exists, else neerja [muted]({'on' if has_gem else 'add gemini key to enable'})[/muted]")
    for i, (key, (edge_name, desc)) in enumerate(speech.VOICES.items(), 1):
        mark = "✔" if key == active else "·"
        console.print(f"  [{mark}] [cyan]{i}. {key}[/cyan] — {desc} "
                      f"[muted]({edge_name})[/muted]")
    for key, name in speech.GEMINI_VOICES.items():
        mark = "✔" if active == f"gemini-{key}" else "·"
        console.print(f"  [{mark}] [cyan]g. gemini-{key}[/cyan] — Gemini "
                      f"native voice [muted]({name}, needs Gemini key)[/muted]")
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
