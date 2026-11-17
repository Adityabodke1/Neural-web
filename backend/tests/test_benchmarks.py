"""Deterministic tests for benchmark score attribution honesty (no network).

Verifies that scores are saved under the model that actually executed and
that failovers add an explicit note to the detail string.

Run: .venv/bin/python -m pytest backend/tests/test_benchmarks.py -v
"""
from __future__ import annotations

import asyncio

from backend.app.database.database import db


def _max_bench_id() -> int:
    with db() as conn:
        row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM benchmarks").fetchone()
    return int(row["m"])


def _cleanup(after_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM benchmarks WHERE id > ?", (after_id,))


def test_failover_attribution_adds_note(monkeypatch):
    """When the requested model fails over, the score is saved under the
    executing model and the detail carries an explicit failover note."""
    from backend.app.benchmarks import harness

    requested = "groq/openai/gpt-oss-120b"
    executed = "groq/openai/gpt-oss-20b"

    def fake_run(model_spec, messages, request_type, max_tokens=500, json_mode=False):
        harness._last_executed_ref["ref"] = executed  # simulated failover
        return "cat\ndog\nbird\nfish"  # passes the 4-line validator

    monkeypatch.setattr(harness, "_run", fake_run)
    before = _max_bench_id()
    try:
        res = asyncio.run(harness.run_benchmark(requested, ["instruction_following"]))
        assert res["results"]["instruction_following"]["executed_on"] == executed
        with db() as conn:
            row = conn.execute(
                "SELECT model_ref, detail FROM benchmarks"
                " WHERE capability='instruction_following' AND id > ?"
                " ORDER BY id DESC LIMIT 1", (before,)).fetchone()
        assert row["model_ref"] == executed
        assert "failed over" in row["detail"]
        assert executed in row["detail"]
    finally:
        _cleanup(before)


def test_no_failover_attributes_to_requested(monkeypatch):
    """Without failover the score is attributed to the requested model and
    no failover note is added."""
    from backend.app.benchmarks import harness

    requested = "groq/openai/gpt-oss-120b"

    def fake_run(model_spec, messages, request_type, max_tokens=500, json_mode=False):
        harness._last_executed_ref["ref"] = requested  # executed as requested
        return "cat\ndog\nbird\nfish"

    monkeypatch.setattr(harness, "_run", fake_run)
    before = _max_bench_id()
    try:
        res = asyncio.run(harness.run_benchmark(requested, ["instruction_following"]))
        assert res["results"]["instruction_following"]["executed_on"] == requested
        with db() as conn:
            row = conn.execute(
                "SELECT model_ref, detail FROM benchmarks"
                " WHERE capability='instruction_following' AND id > ?"
                " ORDER BY id DESC LIMIT 1", (before,)).fetchone()
        assert row["model_ref"] == requested
        assert "failed over" not in row["detail"]
    finally:
        _cleanup(before)
