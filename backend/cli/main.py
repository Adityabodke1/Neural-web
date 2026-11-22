"""NEURAL WEB CLI entrypoint (Typer app) + interactive shell."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from ..app.database import repository as repo
from ..app.database.database import init_db
from .banner import OWNER, VERSION, show_banner, show_header, status_line
from .ui.spin import thinking_status
from .commands import app_cmd, chat_cmd, computer_cmd, data_cmd, models_cmd, research_cmd, voice_cmd
from .theme import THEME

console = Console(theme=THEME)

app = typer.Typer(
    name="neural-web",
    help="NEURAL WEB — Autonomous AI Development & Web Intelligence",
    no_args_is_help=False,
    rich_markup_mode="rich",
)

# All commands live at top level for the documented UX:
#   neural-web research "..." / neural-web sources --last / ...
for fn, name, helptext in [
    (research_cmd.research, "research", "Autonomous AI web research with live progress"),
    (research_cmd.scrape, "scrape", "Scrape a single URL"),
    (research_cmd.crawl, "crawl", "Crawl a domain (depth + dedup + robots.txt)"),
    (research_cmd.extract, "extract", "Extract fields from a URL with provenance"),
    (research_cmd.compare, "compare", "Compare multiple websites"),
    (research_cmd.monitor, "monitor", "Change detection for a page"),
    (data_cmd.sources, "sources", "Source transparency explorer"),
    (data_cmd.explain_source, "explain-source", "Show exactly where a field came from"),
    (data_cmd.history, "history", "Research session history"),
    (data_cmd.search_memory, "search-memory", "Semantic search over past research"),
    (data_cmd.export, "export", "Export results (json/csv/xlsx)"),
    (data_cmd.ask, "ask", "Chat with your research memory"),
    (data_cmd.memory, "memory", "What NEURAL WEB remembers about you (memory.txt)"),
    (data_cmd.conversations, "conversations", "View saved conversations (every chat is stored)"),
    (chat_cmd.chat, "chat", "Talk to the AI directly — type anything, get answers"),
    (models_cmd.model_cmd, "model", "Switch or add the active AI model (simple)"),
    (models_cmd.providers, "providers", "Manage AI providers"),
    (models_cmd.api, "api", "API keys — add, list, test (simple)"),
    (models_cmd.models, "models", "Manage AI models"),
    (models_cmd.usage, "usage", "Token quota dashboard"),
    (models_cmd.benchmarks, "benchmarks", "Model capability benchmarks"),
    (models_cmd.config, "config", "Configuration & emergency controls"),
    (computer_cmd.permissions_cmd, "permissions", "Folder authorization & audit"),
    (computer_cmd.approve, "approve", "Approve a pending level-3 operation"),
    (computer_cmd.coding, "coding", "Autonomous coding agent"),
    (voice_cmd.voice, "voice", "Agentic voice mode — speak a command, the agent acts on your computer"),
    (app_cmd.app, "app", "Open the NEURAL WEB desktop app (chat, agent, research)"),
    (computer_cmd.clear_cache, "clear-cache", "Clear scrape caches"),
]:
    app.command(name=name, help=helptext)(fn)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-V"),
):
    """NEURAL WEB — run without arguments for the interactive shell."""
    if version:
        console.print(f"NEURAL WEB v{VERSION} — created by {OWNER}")
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        interactive()


@app.command("status")
def status():
    """Show models, providers, quotas, engines, sandbox and history."""
    show_banner(console)
    console.print(status_line(console))
    from ..app.computer.sandbox import sandbox_status
    stats = repo.dashboard_stats()
    t = Table(box=box.SIMPLE, header_style="bold cyan")
    t.add_column("Metric"); t.add_column("Value")
    for k, v in stats.items():
        t.add_row(k, str(v))
    console.print(t)
    sb = sandbox_status()
    console.print(f"[muted]sandbox: {sb['mode']} ({sb['backend']})[/muted]")


@app.command("interactive")
def interactive():
    """Interactive shell with autocomplete and command history."""
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory
    except ImportError:
        console.print("[err]prompt-toolkit not installed[/err]"); raise typer.Exit(1)
    init_db()
    show_banner(console)
    from ..app import conversations as conv
    from ..app import long_memory as lm
    from .prompt_style import active_workspace, prompt_markup
    commands = ["research", "scrape", "crawl", "extract", "compare", "monitor",
                "sources", "explain-source", "history", "search-memory", "export",
                "ask", "chat", "api", "providers", "models", "model", "usage",
                "benchmarks", "config", "permissions", "approve", "coding",
                "voice", "memory", "conversations", "clear-cache", "status",
                "exit", "quit", "help"]
    completer = WordCompleter(commands, sentence=True)
    hist_file = Path.home() / ".neural_web_history"
    session = PromptSession(history=FileHistory(str(hist_file)), completer=completer)
    console.print("[muted]type anything — it answers like ChatGPT, and build/"
                  "fix/save requests are executed by the real coding agent. "
                  "Or use commands (TAB autocomplete). 'help' for commands, "
                  "'exit' to leave[/muted]")
    # durable memory: replay earlier conversations so she remembers you
    lm.backfill()
    chat_msgs: list[dict] = [{"role": "system",
                              "content": chat_cmd.system_content("")}]
    past = conv.recent_messages(8)
    if past:
        chat_msgs.extend(past)
        st = conv.stats()
        console.print(f"[muted]🧠 memory: {st['turns']} earlier turns restored "
                      f"from {st['path']}[/muted]")
    command_set = set(commands)
    while True:
        try:
            line = session.prompt(HTML(prompt_markup())).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("[muted]bye[/muted]")
            return
        if not line:
            continue
        if line in ("exit", "quit"):
            console.print("[muted]bye[/muted]")
            return
        if line == "help":
            t = Table(box=box.SIMPLE, header_style="bold cyan")
            t.add_column("Command"); t.add_column("Purpose")
            t.add_row("research \"…\"", "autonomous AI research with live progress")
            t.add_row("scrape URL [--js]", "scrape one page")
            t.add_row("crawl URL --depth N", "crawl a domain")
            t.add_row("extract URL \"fields\"", "field extraction with provenance")
            t.add_row("compare URL1 URL2", "compare sites")
            t.add_row("monitor URL", "change detection")
            t.add_row("sources --last / --record N", "source transparency")
            t.add_row("export --format csv|xlsx|json", "export results")
            t.add_row("ask \"…\"", "chat with your research memory")
            t.add_row("chat", "talk to the AI — type anything, get answers")
            t.add_row("api add groq", "add an API key (hidden prompt, auto-tested)")
            t.add_row("models --import --provider groq", "import models from provider catalog")
            t.add_row("providers add / models add", "configure AI providers & models")
            t.add_row("usage --all", "token quotas")
            t.add_row("benchmarks --run provider/model", "measure model capabilities")
            t.add_row("permissions --add-folder PATH", "authorize folders")
            t.add_row("coding \"task\" --workspace PATH", "autonomous coding agent")
            t.add_row("voice \"command\" / voice --loop", "agentic voice mode — speak, agent acts")
            t.add_row("conversations --last N", "view saved conversations (all chats are saved)")
            t.add_row("config --emergency-stop", "kill all agents & subsystems")
            console.print(t)
            continue
        # Slash commands work right inside the shell too (same handler as chat).
        if line.startswith("/"):
            handled, chat_msgs = chat_cmd._slash(line, chat_msgs)
            continue
        # Not a command? Treat it as a plain question and just answer.
        try:
            first_word = shlex.split(line)[0]
        except ValueError:
            console.print("[err]parse error[/err]")
            continue
        if first_word not in command_set:
            # Build/create/fix/save requests go to the REAL coding agent
            if chat_cmd.maybe_run_agent(line, chat_msgs):
                continue
            chat_msgs.append({"role": "user", "content": line})
            if len(chat_msgs) > 26:
                chat_msgs = [chat_msgs[0]] + chat_msgs[-22:]
            lm.observe(line)
            chat_msgs[0]["content"] = chat_cmd.system_content(
                line, skip_last=max(16, len(chat_msgs) - 2))
            with thinking_status(console):
                try:
                    out = chat_cmd.ai_reply(chat_msgs, line)
                except Exception as e:
                    console.print(f"[err]error:[/] {e}")
                    chat_msgs.pop()
                    continue
            chat_msgs.append({"role": "assistant", "content": out})
            conv.log_turn("user", line, session="shell",
                          workspace=active_workspace())
            conv.log_turn("assistant", out, session="shell",
                          workspace=active_workspace())
            chat_cmd.print_answer(out)
            continue
        _dispatch(line)


def _dispatch(line: str) -> None:
    """Run a CLI command from the interactive shell."""
    try:
        parts = shlex.split(line)
    except ValueError:
        console.print("[err]parse error[/err]"); return
    if not parts:
        return
    import click
    from typer.main import get_command
    try:
        cli = get_command(app)
        cli.main(args=parts, prog_name="neural-web", standalone_mode=False)
    except click.exceptions.Exit:
        pass
    except SystemExit:
        pass
    except click.ClickException as e:
        console.print(f"[err]{e.format_message()}[/err]")
    except Exception as e:
        console.print(f"[err]error:[/err] {e}")


def run() -> None:
    app()


if __name__ == "__main__":
    run()
