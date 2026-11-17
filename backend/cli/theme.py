"""Rich theme: consistent cyan/blue/green/purple dark-terminal aesthetic."""
from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme({
    "brand": "bold cyan",
    "accent": "bold blue",
    "ok": "bold green",
    "warn": "bold yellow",
    "err": "bold red",
    "muted": "dim cyan",
    "purple": "bold magenta",
    "value": "bright_white",
    "label": "cyan",
    "source": "blue underline",
    "verified": "bold green",
    "partial": "bold yellow",
    "unverified": "dim white",
    "contradicted": "bold red",
})

ACCENT_GRADIENT = ["cyan", "bright_blue", "blue", "magenta", "bright_magenta"]

# Shared themed console for the whole CLI
console = Console(theme=THEME)
