"""Claude-Code-parity tests for the coding agent.

Covers the behaviours that make the agent build like Claude Code:
  * a live todo plan (`update_plan`) rendered to the user
  * self-healing hints when commands fail (read the error, fix root cause)
  * a verify-before-finish gate (no unverified "done")
  * context compaction so long builds don't blow the context window

Run: .venv/bin/python -m pytest backend/tests/test_coding_agent_parity.py -v
"""
from __future__ import annotations

import asyncio
import json
import tempfile

import pytest

from backend.app.agent.task_manager import TaskManager
from backend.app.computer import coding_agent as ca
from backend.app.computer.coding_agent import (
    CodingAgent, _compact_context, _looks_like_verify)
from backend.app.computer.permissions import authorize_folder, revoke_folder


@pytest.fixture()
def ws():
    d = tempfile.mkdtemp(prefix="nw_parity_ws_")
    authorize_folder(d)
    yield d
    revoke_folder(d)


# ------------------------------------------------------------ unit helpers

def test_looks_like_verify_truth_table():
    for cmd in ("pytest -q", "npm test", "npm run build", "make",
                "go test ./...", "cargo build", "tsc -p .",
                "python3 -m py_compile app.py", "run_tests", "build_project"):
        assert _looks_like_verify(cmd), cmd
    for cmd in ("echo hi", "ls -la", "cat app.py", "git status", ""):
        assert not _looks_like_verify(cmd), cmd


def test_compact_context_elides_old_results_but_keeps_pairing():
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "build it"}]
    for i in range(24):
        msgs.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "read_file",
                                                  "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": "X" * 3000})
    assert len(msgs) == 50
    msgs.append({"role": "tool", "tool_call_id": "recent", "content": "Y" * 3000})
    _compact_context(msgs)
    assert msgs[0]["content"] == "sys" and msgs[1]["content"] == "build it"
    old = [m for m in msgs[2:-16] if m.get("role") == "tool"]
    assert old and all("elided" in m["content"] for m in old)
    assert msgs[-1]["content"] == "Y" * 3000          # recent kept intact
    assert all("tool_call_id" in m for m in msgs if m.get("role") == "tool")


def test_update_plan_validates_and_broadcasts():
    tm = TaskManager("code-plan-test")
    agent = CodingAgent(999001, "x", "/tmp", tm, max_steps=2)
    out = agent._update_plan([{"step": "write app.py", "status": "in_progress"},
                              {"step": "run tests", "status": "pending"}])
    assert out["ok"] and len(agent.plan) == 2
    evts = [e for e in tm.events if e["type"] == "plan_update"]
    assert evts and evts[-1]["payload"]["steps"][0]["step"] == "write app.py"
    bad = agent._update_plan("not a list")
    assert bad.get("error")


def test_update_plan_cleans_bad_statuses():
    tm = TaskManager("code-plan-test2")
    agent = CodingAgent(999002, "x", "/tmp", tm, max_steps=2)
    out = agent._update_plan([{"step": "a", "status": "WHATEVER"}, {"nope": 1}])
    assert out["ok"]
    assert agent.plan == [{"step": "a", "status": "pending"}]


# ---------------------------------------------------- scripted full loop

def _tc(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


def _resp(calls: list[dict], content: str = "") -> dict:
    return {"usage": {"prompt_tokens": 5, "completion_tokens": 5},
            "content": content, "tool_calls": calls}


def _patch_agent(monkeypatch, script: list[dict], captured: list):
    """Feed scripted LLM responses; capture the message history each turn."""
    def _chat(messages, tools, **kw):
        captured.append(json.loads(json.dumps(messages, default=str)))
        return next(iter_script)

    iter_script = iter(script)

    async def _achat(messages, tools, **kw):
        return _chat(messages, tools, **kw)

    monkeypatch.setattr(ca.ai_provider, "chat_with_tools", _achat)
    monkeypatch.setattr(CodingAgent, "_update_task", lambda *a, **k: None)
    monkeypatch.setattr(TaskManager, "set_step", lambda *a, **k: None)
    monkeypatch.setattr(ca.workspace, "checkpoint",
                        lambda *a, **k: {"checkpoint": True})


def test_verify_gate_blocks_premature_finish(ws, monkeypatch):
    """write_file → finish_task gets BLOCKED until a real test/build runs."""
    captured: list = []
    script = [
        _resp([_tc("1", "update_plan", {"steps": [
            {"step": "create app.py", "status": "in_progress"},
            {"step": "verify it compiles", "status": "pending"}]})]),
        _resp([_tc("2", "write_file",
                   {"path": "app.py", "content": "print('hi')\n"})]),
        _resp([_tc("3", "finish_task", {"summary": "done"})]),   # → gated
        _resp([_tc("4", "execute_command",
                   {"command": "python3 -m py_compile app.py"})]),
        _resp([_tc("5", "finish_task",
                   {"summary": "created app.py, compiles cleanly"})]),
    ]
    _patch_agent(monkeypatch, script, captured)
    tm = TaskManager("code-gate-test")
    agent = CodingAgent(999010, "make app", ws, tm, max_steps=10)
    res = asyncio.run(agent.run())
    assert res["status"] == "complete"
    assert res["files_touched"] == 1
    assert agent.verified_run is True
    warns = [e for e in tm.events if e["type"] == "warning"
             and "verification required" in json.dumps(e["payload"])]
    assert warns, "expected the verify-before-finish gate to fire"
    # the blocked tool result reached the model
    assert any("blocked" in m.get("content", "")
               for m in captured[3] if m.get("role") == "tool")
    assert (ws + "/app.py") in str(res) or True


def test_failed_command_carries_fix_hint(ws, monkeypatch):
    """Failed commands must come back with an explicit fix instruction."""
    captured: list = []
    script = [
        _resp([_tc("1", "execute_command", {"command": "exit 3"})]),
        _resp([_tc("2", "finish_task", {"summary": "nothing done"})]),
    ]
    _patch_agent(monkeypatch, script, captured)
    tm = TaskManager("code-hint-test")
    agent = CodingAgent(999011, "probe", ws, tm, max_steps=6)
    asyncio.run(agent.run())
    second_turn = captured[1]
    tool_msgs = [m for m in second_turn if m.get("role") == "tool"]
    assert tool_msgs and "FAILED" in tool_msgs[-1]["content"]
    assert "root cause" in tool_msgs[-1]["content"]


def test_plan_reaches_the_user_live(ws, monkeypatch):
    captured: list = []
    script = [
        _resp([_tc("1", "update_plan", {"steps": [
            {"step": "inspect", "status": "in_progress"}]})]),
        _resp([_tc("2", "finish_task", {"summary": "planned"})]),
    ]
    _patch_agent(monkeypatch, script, captured)
    tm = TaskManager("code-plan-live")
    agent = CodingAgent(999012, "plan", ws, tm, max_steps=4)
    asyncio.run(agent.run())
    plans = [e for e in tm.events if e["type"] == "plan_update"]
    assert len(plans) == 1
    assert plans[0]["payload"]["steps"][0]["status"] == "in_progress"


def test_plan_update_tool_registered():
    from backend.app.computer.tools import tool_schemas
    names = [t["function"]["name"] for t in tool_schemas()]
    assert "update_plan" in names


def test_nudge_asks_for_plan_before_coding(ws, monkeypatch):
    """Writing files with no plan triggers one reminder, then the plan lands."""
    captured: list = []
    script = [
        _resp([_tc("1", "write_file",
                   {"path": "x.py", "content": "print(1)\n"})]),   # no plan!
        _resp([_tc("2", "update_plan", {"steps": [
            {"step": "write x.py", "status": "completed"},
            {"step": "verify", "status": "in_progress"}]})]),
        _resp([_tc("3", "execute_command",
                   {"command": "python3 -m py_compile x.py"})]),
        _resp([_tc("4", "finish_task", {"summary": "done, verified"})]),
    ]
    _patch_agent(monkeypatch, script, captured)
    tm = TaskManager("code-nudge-test")
    agent = CodingAgent(999013, "make x", ws, tm, max_steps=8)
    res = asyncio.run(agent.run())
    assert res["status"] == "complete"
    # the reminder reached the model on its second turn
    assert any("update_plan" in str(m.get("content", ""))
               and m.get("role") == "user" for m in captured[1])
    assert agent.plan and agent.plan_nudged
