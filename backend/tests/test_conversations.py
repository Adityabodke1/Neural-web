"""Durable conversation history + glowing prompt tests.

Run: .venv/bin/python -m pytest backend/tests/test_conversations.py -v
"""
from __future__ import annotations

import json

from backend.app import conversations as conv
from backend.cli import prompt_style


def test_log_and_recent_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")
    conv.log_turn("user", "build me a website", session="shell")
    conv.log_turn("assistant", "on it", session="shell")
    conv.log_turn("user", "add a dark mode", session="shell")
    msgs = conv.recent_messages(5)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "build me a website"
    st = conv.stats()
    assert st["turns"] == 2 and st["messages"] == 3
    assert st["path"].endswith("conversations.jsonl")


def test_recent_messages_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")
    for i in range(20):
        conv.log_turn("user", f"message {i}")
        conv.log_turn("assistant", f"reply {i}")
    msgs = conv.recent_messages(3)
    assert len(msgs) <= 6
    assert msgs[-1]["content"] == "reply 19"


def test_corrupt_lines_never_break_history(tmp_path, monkeypatch):
    p = tmp_path / "conversations.jsonl"
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", p)
    conv.log_turn("user", "first real message")
    p.write_text(p.read_text() + "{not json}\n\n" + '{"role": "system"}\n',
                 encoding="utf-8")
    conv.log_turn("user", "second real message")
    msgs = conv.recent_messages(5)
    assert [m["content"] for m in msgs] == ["first real message",
                                            "second real message"]


def test_log_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path / "x")
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "x /nope.jsonl")
    conv.log_turn("user", "still fine")      # must not raise
    assert conv.stats()["messages"] == 0


def test_prompt_glows_and_shows_folder(monkeypatch):
    monkeypatch.setattr(prompt_style, "active_workspace",
                        lambda: "/home/zeta/Documents/Neural-web")
    html = prompt_style.prompt_markup()
    assert "neural-web" in html
    assert "Neural-web" in html or "Neural-web" in html
    assert prompt_style.BRAND_BG in html          # glowing brand chip
    assert prompt_style.FOLDER_FG in html         # highlight colour
    assert "❯" in html


def test_prompt_shortens_home(monkeypatch):
    import pathlib
    home = str(pathlib.Path.home())
    out = prompt_style.shorten_path(home + "/Projects/app")
    assert out == "~/Projects/app"


def test_prompt_escapes_html_and_truncates(monkeypatch):
    html = prompt_style.prompt_markup("/tmp/a<b>c&d")
    assert "&lt;b&gt;" in html and "&amp;" in html
    long = prompt_style.shorten_path("/very/" + "x" * 100, max_len=20)
    assert len(long) <= 20 and "…" in long


def test_rich_prompt_has_same_parts():
    rich = prompt_style.rich_prompt("/tmp/buildhere")
    assert "neural-web" in rich and "buildhere" in rich and "❯" in rich


def test_conversations_command_registered():
    from backend.cli.main import app
    from typer.main import get_command
    cli = get_command(app)
    assert "conversations" in cli.commands
    assert "chat" in cli.commands
