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


# --------------------------------------------------- dated memory / clock
def test_format_when_labels():
    from datetime import datetime
    now = datetime(2026, 11, 22, 21, 0)
    assert conv.format_when("2026-11-22T09:05:00", now) == "today, 09:05"
    assert conv.format_when("2026-11-21T23:40:00", now) == "yesterday, 23:40"
    assert conv.format_when("2026-11-18T10:00:00", now) == "4 days ago, 10:00"
    assert conv.format_when("2026-11-01T08:30:00", now) == "Sun 01 Nov 2026, 08:30"
    assert conv.format_when("", now) == ""
    assert conv.format_when("not-a-date", now) == ""


def test_time_context_is_exact_and_timezone_aware():
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    out = conv.time_context(datetime(2026, 11, 22, 21, 5, tzinfo=ist))
    assert "Sunday, 22 November 2026" in out
    assert "09:05 PM" in out
    assert "UTC+05:30" in out
    assert "IST" in out


def test_memory_context_includes_rules_and_clock():
    out = conv.memory_context("BASE-PROMPT")
    assert out.startswith("BASE-PROMPT")
    assert "DATED MEMORY" in out
    assert "yesterday" in out.lower()
    assert "CURRENT DATE/TIME" in out


def test_recent_messages_can_carry_timestamps(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")
    rows = [
        {"ts": "2026-11-21T21:04:00", "role": "user", "content": "build failed"},
        {"ts": "2026-11-21T21:05:00", "role": "assistant", "content": "fix it"},
    ]
    conv.CONV_PATH.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    msgs = conv.recent_messages(2, with_time=True,
                                now=datetime(2026, 11, 22, 9, 0))
    assert msgs[0]["content"] == "[yesterday, 21:04] build failed"
    assert msgs[1]["content"] == "[yesterday, 21:05] fix it"
    # default stays clean for callers/tests that do not need dates
    assert conv.recent_messages(2)[0]["content"] == "build failed"
