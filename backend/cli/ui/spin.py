"""NEURAL WEB's thinking spinner — a firing-neuron animation with themed
phrases (Claude-Code-style whimsy, but our own identity).

Usage:
    from ..ui.spin import thinking_status
    with thinking_status(console):
        ...answer...
"""
from __future__ import annotations

import random

from rich._spinners import SPINNERS
from rich.console import Console

# a pulse traveling between two neurons
SPINNERS.setdefault("neuron", {
    "interval": 110,
    "frames": ["· ✦ ·", "✦ · ✦", "· ✦ ·", "✧ · ✧", "· ✧ ·", "✧ · ✧"],
})

PHRASES = [
    "synapsing",
    "weaving the web",
    "firing neurons",
    "crawling links",
    "connecting nodes",
    "tracing threads",
    "routing packets",
    "deep thinking",
    "untangling knots",
    "brewing insights",
    "waking the web",
    "consulting the network",
]


def thinking_status(console: Console, phrase: str | None = None):
    """A context manager console.status with our neuron spinner and a random
    themed phrase. Non-TTY output renders it as plain text (harmless)."""
    text = phrase if phrase else random.choice(PHRASES)
    # console.status() in this rich version takes a spinner NAME (string)
    return console.status(f"{text}…", spinner="neuron", spinner_style="accent")
