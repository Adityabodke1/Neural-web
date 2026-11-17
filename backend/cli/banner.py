"""Startup banner: ASCII logo + branding + live status."""
from __future__ import annotations

import os
import time

from rich import box
from rich.console import Console
from rich.live import Live
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

# ------------------------------------------------------------- ASCII logo
# Built per-letter so words keep a clean gap: "NEURAL  WEB" (the jammed
# glyphs used to read like "NEUBALWER" on some fonts).
_LETTERS: dict[str, list[str]] = {
    "N": [" ███╗   ██╗", " ████╗  ██║", " ██╔██╗ ██║",
          " ██║╚██╗██║", " ██║ ╚████║", " ╚═╝  ╚═══╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "U": ["██╗   ██╗", "██║   ██║", "██║   ██║", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "W": ["██╗    ██╗", "██║    ██║", "██║ █╗ ██║",
          "██║███╗██║", "╚███╔███╔╝", " ╚══╝╚══╝ "],
    "B": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██████╔╝", "╚═════╝ "],
}


def _build_logo_rows() -> list[str]:
    """Rows of the logo. Every letter's rows are normalized to the letter's
    max width BEFORE joining — otherwise trailing spaces differ per row and
    the second word starts at a different column on each row (the staggered
    'overlap' garble)."""
    def norm(ch: str) -> list[str]:
        rows = _LETTERS[ch]
        w = max(len(r) for r in rows)
        # +1 = kerning space so neighbouring letters (R/B look alike) separate
        return [r.ljust(w + 1) for r in rows]

    w1_rows = ["".join(seg) for seg in zip(*(norm(c) for c in "NEURAL"))]
    w2_rows = ["".join(seg) for seg in zip(*(norm(c) for c in "WEB"))]
    # 1-space letter kerning (R vs B blur together when jammed) + word gap
    return [" " + a + "   " + b for a, b in zip(w1_rows, w2_rows)]


LOGO_ROWS = _build_logo_rows()
LOGO = "\n".join(LOGO_ROWS)

# cyan → white-hot → magenta wave palette
_PALETTE = ["#0e7490", "#06b6d4", "#22d3ee", "#cffafe",
            "#f5d0fe", "#e879f9", "#c026d3"]


def _logo_text(frame: float = 0.0, animate: bool = False) -> Text:
    """Render the logo; with a moving light wave when animating, and a frozen
    cyan→magenta gradient when static."""
    rows = LOGO_ROWS
    t = Text()
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == " ":
                t.append(" ")
                continue
            if animate:
                phase = ((c + r * 1.6) - frame * 2.6) % 30.0
                idx = int(phase / 30.0 * len(_PALETTE)) % len(_PALETTE)
                style = _PALETTE[idx]
            else:
                # frozen diagonal gradient: cyan top-left → magenta bottom-right
                pos = (c / max(len(row), 1) + r / 6.0) / 2.0
                style = _PALETTE[min(int(pos * len(_PALETTE)), len(_PALETTE) - 1)]
            t.append(ch, style=style)
        t.append("\n")
    return t


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


def _banner_grid(logo: Text) -> Panel:
    # subtitle BELOW the logo: the side-by-side grid made the row too wide,
    # and Rich wrapped the logo lines mid-glyph (the "NEUBALWER" garble)
    subtitle = Text.assemble(
        ("NEURAL WEB\n", "bold magenta"),
        ("AUTONOMOUS AI DEVELOPMENT & WEB INTELLIGENCE\n", "bold blue"),
        (f"v{VERSION}  •  Linux/Ubuntu ready", "muted"),
    )
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="left", vertical="middle")
    grid.add_column(justify="left", vertical="middle")
    grid.add_row(logo)
    grid.add_row(subtitle)
    return Panel(grid, border_style="cyan", box=box.HEAVY, padding=(0, 1))


def show_banner(console: Console) -> None:
    """The big NEURAL WEB logo + owner branding (no status table).

    Interactive terminals get a one-shot 'neural pulse' animation — a wave of
    light sweeps across the letters, then settles into a frozen cyan→magenta
    gradient (the signal traveling through the network). Set NW_NO_ANIM=1 to
    disable. Non-TTY output (pipes, logs) always gets the static banner."""
    logo_width = max(len(r) for r in LOGO_ROWS) + 4
    if console.width and console.width < logo_width + 2:
        # too narrow for the big logo — never wrap-garble it
        show_header(console, "type 'help' for commands")
        return
    animate = (console.is_terminal
               and not os.environ.get("NW_NO_ANIM")
               and settings is not None)
    if not animate:
        console.print(_banner_grid(_logo_text(animate=False)))
    else:
        try:
            with Live(_banner_grid(_logo_text(0.0, True)), console=console,
                      refresh_per_second=24, transient=True) as live:
                for frame in range(14):
                    live.update(_banner_grid(_logo_text(frame, True)))
                    time.sleep(0.045)
            # frozen gradient end-state
            console.print(_banner_grid(_logo_text(animate=False)))
        except Exception:
            console.print(_banner_grid(_logo_text(animate=False)))
    console.print(Panel(
        Text.assemble(("CREATED BY  ", "dim"), (OWNER, "bold bright_magenta"),
                      ("   •   AUTONOMOUS WEB RESEARCH + AI CODING", "dim")),
        border_style="magenta", box=box.SIMPLE, padding=(0, 1)))
    console.print()
