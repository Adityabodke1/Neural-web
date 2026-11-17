"""Startup banner: ASCII logo + branding + live status."""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..app.computer.sandbox import sandbox_status
from ..app.database import repository as repo
from ..app.quotas.tracker import quota_tracker
from ..app.providers import manager as pm
from ..app.search.search_manager import search_manager
from ..app.scraping.engine import scrape_engine
from ..app.config import settings

OWNER = "ADITYA BODKE"
VERSION = "2.0.0"

LOGO = r"""
 ███╗   ██╗███████╗██╗   ██╗██████╗  █████╗ ██╗     ██╗    ██╗███████╗██████╗
 ████╗  ██║██╔════╝██║   ██║██╔══██╗██╔══██╗██║     ██║    ██║██╔════╝██╔══██╗
 ██╔██╗ ██║█████╗  ██║   ██║██████╔╝███████║██║     ██║ █╗ ██║█████╗  ██████╔╝
 ██║╚██╗██║██╔══╝  ██║   ██║██╔══██╗██╔══██║██║     ██║███╗██║██╔══╝  ██╔══██╗
 ██║ ╚████║███████╗╚██████╔╝██████╔╝██║  ██║███████╗╚███╔███╔╝███████╗██║  ██║
 ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚══╝╚══╝ ╚══════╝╚═╝  ╚═╝
"""


def status_line(console: Console) -> Table:
    models = pm.list_models()
    active = None
    try:
        active = pm.resolve_model("auto")
    except Exception:
        active = None
    t = Table(show_header=False, box=box.SIMPLE, pad_edge=False, padding=(0, 1))
    t.add_column(style="label", no_wrap=True)
    t.add_column(style="value")
    active_label = "[ok]✔ ready[/ok]" if active else "[warn]no model configured — run: neural-web models add[/warn]"
    t.add_row("[label]Active model[/]", active["id"] if active else active_label)
    t.add_row("[label]Providers[/]", ", ".join(
        f"{p['id']}({'✔' if p['api_key_present'] else '·'})" for p in pm.list_providers()) or "none")
    q = quota_tracker.overview()["total"]
    if q.get("aggregated"):
        t.add_row("[label]Quota[/]",
                  f"{q['used_tokens']:,}/{q['limit_tokens']:,} tokens ({q['percent_used']}%) — {q['period']}")
    else:
        used = repo.usage_summary().get("total_tokens", 0)
        t.add_row("[label]Tokens used (lifetime)[/]", f"{used:,} — set model limits via: neural-web usage set-limit")
    t.add_row("[label]Search engine[/]", search_manager.engine_name)
    t.add_row("[label]Scraper[/]", scrape_engine.name)
    sb = sandbox_status()
    t.add_row("[label]Sandbox[/]", f"{sb['mode']} ({sb['backend']})")
    t.add_row("[label]Sessions[/]", str(repo.dashboard_stats()["sessions"]))
    return t


def show_header(console: Console, extra: str = "") -> None:
    """One compact Claude-Code-style header line (no giant logo)."""
    try:
        active = pm.resolve_model("auto")
        model = active["id"] if active else "no model"
    except Exception:
        model = "no model"
    line = Text.assemble(
        ("NEURAL WEB", "bold cyan"),
        (f" v{VERSION}", "muted"),
        ("  ·  ", "muted"), (OWNER, "bold bright_magenta"),
        ("  ·  ", "muted"), (model, "cyan"),
    )
    if extra:
        line.append("  ·  ")
        line.append(Text(extra, style="dim"))
    console.print(line)
    console.print()


def show_banner(console: Console) -> None:
    """The big NEURAL WEB logo + owner branding (no status table)."""
    logo = Text(LOGO, style="bold cyan")
    subtitle = Text.assemble(
        ("NEURAL WEB\n", "bold magenta"),
        ("AUTONOMOUS AI DEVELOPMENT & WEB INTELLIGENCE\n", "bold blue"),
        (f"v{VERSION}  •  Linux/Ubuntu ready", "muted"),
    )
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="left", vertical="middle")
    grid.add_column(justify="left", vertical="middle")
    grid.add_row(logo, subtitle)
    console.print(Panel(grid, border_style="cyan", box=box.HEAVY, padding=(0, 1)))
    console.print(Panel(
        Text.assemble(("CREATED BY  ", "dim"), (OWNER, "bold bright_magenta"),
                      ("   •   AUTONOMOUS WEB RESEARCH + AI CODING", "dim")),
        border_style="magenta", box=box.SIMPLE, padding=(0, 1)))
    console.print()
