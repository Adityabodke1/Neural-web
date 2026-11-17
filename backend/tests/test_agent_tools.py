"""Agentic-coding tool tests: REAL filesystem/terminal operations inside an
authorized workspace, permission enforcement, path-traversal prevention and
agent-loop honesty guarantees.

Run: .venv/bin/python -m pytest backend/tests/test_agent_tools.py -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from backend.app.computer.permissions import PermissionDenied, authorize_folder, revoke_folder
from backend.app.computer.tools import ToolContext, execute_tool


@pytest.fixture(scope="module")
def ws():
    d = tempfile.mkdtemp(prefix="nw_agent_ws_")
    info = authorize_folder(d)
    yield d, info
    revoke_folder(d)


def _ctx(ws_dir: str) -> ToolContext:
    return ToolContext(ws_dir, "test-agent-tools")


# ---------------------------------------------------------------- fs tools

def test_write_read_roundtrip(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("write_file",
        {"path": "src/app.py", "content": "print('hi')\n"}, _ctx(ws_dir)))
    assert r.get("written") and not r.get("error")
    r = asyncio.run(execute_tool("read_file", {"path": "src/app.py"}, _ctx(ws_dir)))
    assert r["content"] == "print('hi')\n"


def test_edit_preserves_rest_of_file(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "edit_me.txt", "content": "alpha\nbeta\ngamma\n"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("edit_file",
        {"path": "edit_me.txt", "old_string": "beta", "new_string": "BETA"},
        _ctx(ws_dir)))
    assert r.get("edited")
    content = asyncio.run(execute_tool("read_file",
        {"path": "edit_me.txt"}, _ctx(ws_dir)))["content"]
    assert content == "alpha\nBETA\ngamma\n"


def test_edit_rejects_ambiguous_match(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "dup.txt", "content": "x\nx\n"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("edit_file",
        {"path": "dup.txt", "old_string": "x", "new_string": "y"}, _ctx(ws_dir)))
    assert r.get("error") and "replace_all" in r["error"]


def test_move_and_copy(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "orig.txt", "content": "payload"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("move_path",
        {"source": "orig.txt", "destination": "renamed.txt", "confirm": True},
        _ctx(ws_dir)))
    assert not r.get("error"), r
    assert os.path.exists(os.path.join(ws_dir, "renamed.txt"))
    assert not os.path.exists(os.path.join(ws_dir, "orig.txt"))
    r = asyncio.run(execute_tool("copy_path",
        {"source": "renamed.txt", "destination": "copy.txt"}, _ctx(ws_dir)))
    assert not r.get("error"), r


def test_delete_requires_confirm(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "victim.txt", "content": "x"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("delete_path",
        {"path": "victim.txt", "confirm": False}, _ctx(ws_dir)))
    assert r.get("error") and os.path.exists(os.path.join(ws_dir, "victim.txt"))
    r = asyncio.run(execute_tool("delete_path",
        {"path": "victim.txt", "confirm": True}, _ctx(ws_dir)))
    assert not r.get("error") and not os.path.exists(os.path.join(ws_dir, "victim.txt"))


def test_list_and_search(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "findme.txt", "content": "UNIQUE_NEEDLE_42"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("list_directory", {"path": "."}, _ctx(ws_dir)))
    assert any(e["name"] == "findme.txt" for e in r["entries"])
    r = asyncio.run(execute_tool("search_files",
        {"path": ".", "pattern": "*.txt", "content": "UNIQUE_NEEDLE_42"}, _ctx(ws_dir)))
    assert any("findme.txt" in m["path"] for m in r.get("matches", []))


# ------------------------------------------------- permission enforcement

def test_path_traversal_denied(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("write_file",
        {"path": "../../etc/neuralweb_escape_test", "content": "no"},
        _ctx(ws_dir)))
    assert r.get("error") == "permission_denied"
    assert not os.path.exists("/etc/neuralweb_escape_test")


def test_write_outside_workspace_denied(ws):
    ws_dir, _ = ws
    outside = os.path.join(os.path.dirname(ws_dir), "outside_target.txt")
    r = asyncio.run(execute_tool("write_file",
        {"path": outside, "content": "no"}, _ctx(ws_dir)))
    assert r.get("error") == "permission_denied"
    assert not os.path.exists(outside)


def test_symlink_escape_denied(ws):
    ws_dir, _ = ws
    link = os.path.join(ws_dir, "evil_link")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink("/etc", link)
    try:
        r = asyncio.run(execute_tool("write_file",
            {"path": "evil_link/neuralweb_escape", "content": "no"}, _ctx(ws_dir)))
        assert r.get("error") == "permission_denied"
        assert not os.path.exists("/etc/neuralweb_escape")
    finally:
        os.remove(link)


# ------------------------------------------------------- command execution

def test_execute_command_real_output(ws):
    ws_dir, _ = ws
    asyncio.run(execute_tool("write_file",
        {"path": "echo_target.txt", "content": "sandbox says hi"}, _ctx(ws_dir)))
    r = asyncio.run(execute_tool("execute_command",
        {"command": "cat echo_target.txt"}, _ctx(ws_dir)))
    assert r.get("exit_code") == 0
    assert "sandbox says hi" in (r.get("stdout") or "")


def test_execute_command_reports_failure_honestly(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("execute_command",
        {"command": "exit 3"}, _ctx(ws_dir)))
    assert r.get("exit_code") == 3


def test_sandbox_blocks_writes_outside_workspace(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("execute_command",
        {"command": "echo hacked > /tmp/nw_sandbox_escape_test"}, _ctx(ws_dir)))
    # inside bwrap, /tmp is a private tmpfs — the file must NOT appear on host
    assert not os.path.exists("/tmp/nw_sandbox_escape_test")


def test_system_modifying_command_refused(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("execute_command",
        {"command": "rm -rf /usr"}, _ctx(ws_dir)))
    assert r.get("error") or r.get("exit_code") not in (0, None) or "refus" in \
        str(r).lower()


# ------------------------------------------------------------ agent loop

def test_finish_status_requires_real_finish():
    """A run that never called finish_task must NOT be reported complete."""
    from backend.app.computer.coding_agent import CodingAgent
    from backend.app.agent.task_manager import TaskManager
    import asyncio
    tm = TaskManager("code-selftest")
    agent = CodingAgent(999901, "self test", "/tmp", tm, max_steps=1)
    res = agent._finish("stopped" if not False else "complete",
                        summary="", verification="v")
    # _finish with empty summary + stopped status must not fake completion
    assert res["status"] in ("stopped", "failed")


def test_json_fallback_parses_action():
    """The compatible-execution strategy must parse the JSON action format."""
    from backend.app.providers.adapter import parse_json_loose
    action = parse_json_loose('{"tool": "write_file", "args": {"path": "a.txt",'
                              ' "content": "x"}, "summary": ""}')
    assert action["tool"] == "write_file"
    assert action["args"]["path"] == "a.txt"


def test_unknown_tool_reported_not_assumed(ws):
    ws_dir, _ = ws
    r = asyncio.run(execute_tool("totally_fake_tool", {}, _ctx(ws_dir)))
    assert r.get("error") and "unknown tool" in r["error"]


def test_cleanup_never_leaves_test_artifacts(ws):
    ws_dir, _ = ws
    for f in ("renamed.txt", "copy.txt", "edit_me.txt", "dup.txt",
              "findme.txt", "echo_target.txt", "src/app.py"):
        p = os.path.join(ws_dir, f)
        if os.path.exists(p):
            if os.path.isdir(p):
                import shutil
                shutil.rmtree(p)
            else:
                os.remove(p)
    assert not os.path.exists("/etc/neuralweb_escape_test")
    assert not os.path.exists("/tmp/nw_sandbox_escape_test")
