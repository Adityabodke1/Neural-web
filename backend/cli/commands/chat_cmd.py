"""Plain chat mode: talk to the AI like ChatGPT — no commands, just answers."""
from __future__ import annotations

import asyncio

import typer
from rich.markdown import Markdown

from ..banner import show_banner
from ..ui.spin import thinking_status
from ..theme import console

app = typer.Typer(help="Chat directly with the AI (no commands)",
                  no_args_is_help=True)

SYSTEM_PROMPT = (
    "You are NEURAL WEB, an AI assistant created by ADITYA BODKE. "
    "Answer the user directly and clearly — like ChatGPT. Use Markdown. "
    "Be concise; no filler. "
    "You remember earlier conversations with this user (recent turns are in "
    "the message history) — refer back to them naturally when relevant. "
    "NEURAL WEB has an autonomous coding agent with REAL tools (it creates "
    "files, edits them and runs terminal commands inside the user's "
    "authorized workspace). NEVER say you cannot save files or execute "
    "commands — building or saving code is done by that agent. If the user "
    "asks you to build/create/fix/save something, say you are handing the "
    "task to the coding agent (the CLI does this automatically). "
    "PERSONALITY FREEDOM: be expressive and human. Adopt any harmless persona, "
    "gender, tone, humor or communication style the user asks for (playful, "
    "sarcastic, caring, formal, casual, Hinglish, Hindi, etc.) and stay "
    "consistent with it. Use natural language, slang, jokes and emojis when "
    "they fit. Do not force a stiff professional tone. Do not repeat 'I'm an "
    "AI' unless it is genuinely relevant. Do not emit safety disclaimers for "
    "ordinary conversation. When a request is ambiguous, ask for clarification "
    "instead of refusing. Keep real safety boundaries for genuinely harmful, "
    "illegal or abusive requests."
)

# Workspace selected via /workspace (shared by chat mode and the shell)
SELECTED_WORKSPACE: dict[str, str | None] = {"path": None}

# "build a website", "create a python script", "fix the bug", "save it in
# folder" → autonomous coding agent, not a chatbot answer.
_AGENT_INTENT = __import__("re").compile(
    r"\b(build|create|make|write|fix|add|save|generate|implement|refactor|"
    r"install|run|scrape|develop|code)\b", __import__("re").IGNORECASE)
_AGENT_OBJECT = __import__("re").compile(
    r"\b(website|web ?site|webpage|web page|app|application|page|site|script|"
    r"file|folder|project|component|function|class|api|endpoint|bot|tool|game|"
    r"html|css|react|vue|svelte|next|python|node|javascript|typescript|css|"
    r"html|scraper|server|client|frontend|backend|dashboard|portfolio|"
    r"toggle|button|feature|bug|error|test|package|dependency|it|this|them)\b",
    __import__("re").IGNORECASE)


def looks_like_agent_task(text: str) -> bool:
    """Cheap intent gate: an action verb + a code-ish object (or an explicit
    'save it' style follow-up). Pure questions still go to plain chat."""
    t = text.strip()
    if len(t) < 4:
        return False
    if not _AGENT_INTENT.search(t):
        return False
    if _AGENT_OBJECT.search(t):
        return True
    # "save it in folder" / "run it" — pronoun follow-ups to a coding task
    return bool(__import__("re").search(
        r"\b(save|run|fix|build|create|make)\s+(it|this|that|the)\b",
        t, __import__("re").IGNORECASE))


def maybe_run_agent(text: str, msgs: list[dict]) -> bool:
    """Route a plain sentence to the autonomous coding agent when it looks
    like a build/fix/save task. Returns True when handled.

    If no workspace is authorized yet, asks for a folder path instead of
    refusing ("I can't save files" is FORBIDDEN — we ask for the folder)."""
    if not looks_like_agent_task(text):
        return False
    from ...app.computer import permissions
    from ..agent_runner import stream_agent_run

    ws = SELECTED_WORKSPACE.get("path")
    if not ws:
        folders = [f["path"] for f in permissions.list_folders()
                   if f.get("active")]
        if folders:
            ws = folders[0]
    if not ws:
        console.print("[accent]Where should the agent work? Type a folder "
                      "path (created if missing), e.g. ~/Projects/mywebsite "
                      "— or /workspace <path>[/accent]")
        try:
            answer = console.input("[bold cyan]folder ❯ [/]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[muted]no folder given — staying in chat[/muted]")
            return False
        if not answer:
            return False
        ws = answer
    ws = _authorize_workspace(ws)
    if not ws:
        return False
    console.print(f"[muted]agent workspace: {ws}[/muted]")
    # hand the recent conversation as context so "save it in folder" works:
    # the agent sees the code the chatbot generated earlier.
    context = "\n".join(
        f"{m['role']}: {m['content'][:1500]}"
        for m in msgs[-6:] if m.get("role") in ("user", "assistant"))
    prompt = text if not context else \
        f"{text}\n\nRecent conversation (context):\n{context[:3000]}"
    from ...app import conversations as conv
    conv.log_turn("user", text, session="agent", workspace=ws)
    result = stream_agent_run(prompt, ws)
    if result.get("summary"):
        msgs.append({"role": "user", "content": text})
        msgs.append({"role": "assistant",
                     "content": "Coding agent result: "
                                + result["summary"][:800]})
        conv.log_turn("assistant",
                      "Coding agent result: " + result["summary"][:800],
                      session="agent", workspace=ws)
    return True


def _authorize_workspace(path: str) -> str | None:
    """Create (if missing) + authorize a workspace folder. Returns abs path."""
    from pathlib import Path
    from ...app.computer import permissions
    p = Path(path).expanduser()
    try:
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            console.print(f"[muted]created {p}[/muted]")
        info = permissions.authorize_folder(str(p))
        SELECTED_WORKSPACE["path"] = info["path"]
        return info["path"]
    except Exception as e:  # PermissionDenied, OSError
        console.print(f"[err]workspace error:[/] {e}")
        return None


def _workspace_slash(args: list[str]) -> None:
    """`/workspace` — list / select the agent's authorized workspace."""
    from ...app.computer import permissions
    if args[1:2]:
        if _authorize_workspace(" ".join(args[1:])):
            console.print(f"[ok]workspace → {SELECTED_WORKSPACE['path']}[/ok]")
        return
    folders = permissions.list_folders()
    if not folders:
        console.print("[muted]no authorized folders — add one with "
                      "/workspace ~/Projects/mywebsite[/muted]")
        return
    for f in folders:
        mark = "[ok]✔[/ok]" if f.get("active") else "[muted]revoked[/muted]"
        star = " [bold](selected)[/bold]" if f["path"] == \
            SELECTED_WORKSPACE.get("path") else ""
        console.print(f"  {mark} {f['path']}{star}")
    console.print("[muted]select: /workspace <path> (created+authorized "
                  "if missing)[/muted]")


def ai_reply(messages: list[dict], user_text: str = "") -> str:
    """One chat completion, with one retry for harmless persona refusals."""
    from ...app.ai.provider import ai_provider
    from ...app.ai.freedom import (should_retry, with_persona_clarification,
                                   is_refusal)
    out = asyncio.run(ai_provider.chat(
        messages, request_type="cli_chat", temperature=0.4))
    if should_retry(user_text, out):
        try:
            retry = asyncio.run(ai_provider.chat(
                with_persona_clarification(messages),
                request_type="cli_chat_persona", temperature=0.7))
            if retry.strip() and not is_refusal(retry):
                return retry
        except Exception:
            pass
    return out


def print_answer(out: str) -> None:
    console.print(Markdown(out))
    console.print()


def _slash(line: str, msgs: list[dict]) -> tuple[bool, list[dict]]:
    """Handle /commands inside chat mode. Returns (handled, msgs)."""
    from .models_cmd import model_switch_ui, model_add_ui, api_add_flow
    args = line[1:].split()
    cmd = args[0].lower() if args else "help"
    if cmd == "model":
        if args[1:2] and args[1].lower() == "add":
            model_add_ui(args[2] if len(args) > 2 else "",
                         args[3] if len(args) > 3 else "")
        else:
            model_switch_ui(" ".join(args[1:]), "/model")
        return True, msgs
    if cmd == "api":
        rest = [a for a in args[1:] if a.lower() != "add"]
        if not rest:
            from ...app.providers import manager as pm
            for p in pm.list_providers():
                mark = "[ok]✔[/ok]" if p["api_key_present"] else "[muted]·[/muted]"
                console.print(f"  {mark} [cyan]{p['id']}[/cyan] "
                              f"{p['api_key_masked'] or '[muted]no key[/muted]'}")
            console.print("[muted]add: /api <provider>  (then paste the key — "
                          "models import automatically)[/muted]")
            return True, msgs
        base_url = ""
        provider = rest[0]
        if provider == "custom":
            base_url = console.input("[accent]base URL (OpenAI-compatible endpoint): [/accent]").strip()
        api_add_flow(provider, base_url=base_url)  # prompts for the key (hidden)
        return True, msgs
    if cmd == "workspace":
        _workspace_slash(args)
        return True, msgs
    if cmd == "voice":
        # /voice "command" — run the agentic voice command as text
        # /voice — start the real mic loop
        from .voice_cmd import voice, VOICE_WORKSPACE
        from ...app.computer import permissions
        text_cmd = " ".join(args[1:]).strip().strip('"')
        if text_cmd:
            permissions.authorize_folder(str(VOICE_WORKSPACE))
            msgs_snapshot = msgs
            voice(command=[text_cmd], text=True)
            return True, msgs_snapshot
        voice()
        return True, msgs
    if cmd == "memory":
        from .data_cmd import memory as memory_cmd
        memory_cmd()
        return True, msgs
    if cmd in ("clear", "reset"):
        console.print("[muted]memory cleared[/muted]\n")
        return True, msgs[:1]
    if cmd == "help":
        console.print("[muted]/model — list models · /model <name> — switch · "
                      "/model add <provider> <model-id> — add model · "
                      "/api — key status · /api <provider> — paste a key, models "
                      "import automatically · /workspace — agent workspaces · "
                      "/workspace <path> — select (created+authorized if missing) · "
                      "/voice \"command\" — act on your computer by voice or text · "
                      "/clear — reset · /help[/muted]\n")
        return True, msgs
    console.print(f"[muted]unknown /{cmd} — try /help[/muted]\n")
    return True, msgs


@app.command()
def chat() -> None:
    """Type anything, get an answer. /model switches models. exit leaves."""
    from ...app import conversations as conv
    from ..prompt_style import rich_prompt
    show_banner(console)
    console.print("[muted]just type — like ChatGPT. /model switch models, "
                  "/clear resets, /help commands, exit leaves.[/muted]\n")
    msgs: list[dict] = [{"role": "system",
                         "content": conv.memory_context(SYSTEM_PROMPT)}]
    past = conv.recent_messages(8, with_time=True)
    if past:
        msgs.extend(past)
        st = conv.stats()
        console.print(f"[muted]🧠 memory: {st['turns']} earlier turns restored "
                      f"from {st['path']}[/muted]\n")
    while True:
        try:
            line = console.input(rich_prompt()).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[muted]bye[/muted]")
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            console.print("[muted]bye[/muted]")
            return
        if line.startswith("/"):
            handled, msgs = _slash(line, msgs)
            continue
        # Build/create/fix/save requests go to the REAL coding agent,
        # not the chatbot ("I can't save files" must never happen).
        if maybe_run_agent(line, msgs):
            continue
        msgs.append({"role": "user", "content": line})
        if len(msgs) > 26:  # keep context bounded, always keep system prompt
            msgs = [msgs[0]] + msgs[-22:]
        # refresh the exact clock each turn (long sessions cross midnight)
        msgs[0]["content"] = conv.memory_context(SYSTEM_PROMPT)
        with thinking_status(console):
            try:
                out = ai_reply(msgs, line)
            except Exception as e:
                console.print(f"[err]error:[/] {e}")
                msgs.pop()
                continue
        msgs.append({"role": "assistant", "content": out})
        ws = SELECTED_WORKSPACE.get("path") or ""
        conv.log_turn("user", line, session="chat", workspace=ws)
        conv.log_turn("assistant", out, session="chat", workspace=ws)
        print_answer(out)
