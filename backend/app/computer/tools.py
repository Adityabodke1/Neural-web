"""Unified agent tool registry.

Every tool: name, description, JSON schema, permission level, timeout, audit.
Model-generated arguments are always validated by the backend implementations —
never trusted directly (prompt-injection defence: external content is DATA).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from . import browser_agent, filesystem, permissions, workspace
from .permissions import PermissionDenied, LEVEL_WORKSPACE_WRITE
from .terminal import terminal_engine
from ..config import settings

log = logging.getLogger("neuralweb.tools")


class ToolContext:
    def __init__(self, workspace: str, session_id: str = "") -> None:
        self.workspace = workspace
        self.session_id = session_id


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


TOOLS = [
    # ---------------- filesystem ----------------
    {"name": "read_file", "level": 1,
     "description": "Read a text file inside an authorized folder.",
     "parameters": _obj({"path": {"type": "string"}}, ["path"])},
    {"name": "list_directory", "level": 1,
     "description": "List a directory inside an authorized folder.",
     "parameters": _obj({"path": {"type": "string"}}, ["path"])},
    {"name": "search_files", "level": 1,
     "description": "Search files by glob pattern and optionally by content.",
     "parameters": _obj({"path": {"type": "string"}, "pattern": {"type": "string"},
                         "content": {"type": "string"}}, ["path", "pattern"])},
    {"name": "write_file", "level": 2,
     "description": "Create or overwrite a file (atomic write).",
     "parameters": _obj({"path": {"type": "string"}, "content": {"type": "string"}},
                        ["path", "content"])},
    {"name": "edit_file", "level": 2,
     "description": "Find-and-replace exact text in a file. old_string must be unique.",
     "parameters": _obj({"path": {"type": "string"}, "old_string": {"type": "string"},
                         "new_string": {"type": "string"},
                         "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"])},
    {"name": "create_directory", "level": 2,
     "description": "Create a directory (recursively) inside an authorized folder.",
     "parameters": _obj({"path": {"type": "string"}}, ["path"])},
    {"name": "delete_path", "level": 2,
     "description": "Delete a file or directory. confirm=true is mandatory "
                    "(destructive). recursive=true for directories.",
     "parameters": _obj({"path": {"type": "string"}, "recursive": {"type": "boolean"},
                         "confirm": {"type": "boolean"}}, ["path", "confirm"])},
    {"name": "move_path", "level": 2,
     "description": "Move or rename a file/directory.",
     "parameters": _obj({"source": {"type": "string"}, "destination": {"type": "string"},
                         "confirm": {"type": "boolean"}}, ["source", "destination"])},
    {"name": "copy_path", "level": 2,
     "description": "Copy a file or directory.",
     "parameters": _obj({"source": {"type": "string"}, "destination": {"type": "string"}},
                        ["source", "destination"])},
    # ---------------- terminal / development ----------------
    {"name": "execute_command", "level": 2,
     "description": "Run a shell command in the workspace sandbox (timed, audited, "
                    "policy-checked). System-modifying commands are refused.",
     "parameters": _obj({"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}},
                        ["command"])},
    {"name": "start_process", "level": 2,
     "description": "Start a long-running process (e.g. dev server). Returns process_id.",
     "parameters": _obj({"command": {"type": "string"}}, ["command"])},
    {"name": "read_process_output", "level": 1,
     "description": "Read output of a started process.",
     "parameters": _obj({"process_id": {"type": "integer"}}, ["process_id"])},
    {"name": "stop_process", "level": 2,
     "description": "Stop a process this agent started.",
     "parameters": _obj({"process_id": {"type": "integer"}}, ["process_id"])},
    {"name": "install_dependency", "level": 2,
     "description": "Install a PROJECT-LOCAL dependency (pip into workspace venv, or "
                    "npm/yarn install). Global installs and sudo are refused.",
     "parameters": _obj({"manager": {"type": "string", "enum": ["pip", "npm", "yarn", "pnpm"]},
                         "packages": {"type": "array", "items": {"type": "string"}}},
                        ["manager", "packages"])},
    {"name": "run_tests", "level": 2,
     "description": "Run project tests (pytest / npm test) in the workspace.",
     "parameters": _obj({}, [])},
    {"name": "build_project", "level": 2,
     "description": "Run the project build (npm run build / python -m build ...).",
     "parameters": _obj({"command": {"type": "string"}}, [])},
    {"name": "inspect_project", "level": 1,
     "description": "Inspect workspace status, languages, git state and file tree.",
     "parameters": _obj({}, [])},
    {"name": "update_plan", "level": 1,
     "description": "Maintain the visible task plan (todo list, Claude-Code style). "
                    "Call this FIRST with the full list of steps (status 'pending'), "
                    "then update as you work: mark the current step 'in_progress' and "
                    "finished steps 'completed'. The user watches this list live — "
                    "keep steps short and concrete.",
     "parameters": _obj({"steps": {"type": "array", "items": {"type": "object",
         "properties": {"step": {"type": "string"},
                        "status": {"type": "string",
                                   "enum": ["pending", "in_progress", "completed"]}},
         "required": ["step", "status"]}}}, ["steps"])},
    # ---------------- system info ----------------
    {"name": "detect_os", "level": 1,
     "description": "Detect the operating system and architecture.",
     "parameters": _obj({}, [])},
    {"name": "get_system_information", "level": 1,
     "description": "OS, kernel, arch, installed toolchains. No secrets are returned.",
     "parameters": _obj({}, [])},
    {"name": "check_installed_tools", "level": 1,
     "description": "Check whether dev tools (git, node, python, docker...) exist.",
     "parameters": _obj({"tools": {"type": "array", "items": {"type": "string"}}}, [])},
    {"name": "request_elevation", "level": 3,
     "description": "Request an elevated/system operation. Creates an approval request "
                    "the USER must approve; nothing executes automatically.",
     "parameters": _obj({"description": {"type": "string"}}, ["description"])},
    {"name": "git_checkpoint", "level": 2,
     "description": "Create a git checkpoint commit of the current workspace state.",
     "parameters": _obj({"message": {"type": "string"}}, [])},
    {"name": "git_diff", "level": 1,
     "description": "Show current uncommitted changes in the workspace.",
     "parameters": _obj({}, [])},
    # ---------------- computer use (voice mode) ----------------
    {"name": "launch_app", "level": 2,
     "description": "Open a desktop application on the user's computer so they "
                    "can SEE it (brave, chrome, firefox, files, calculator, "
                    "code, terminal, settings, vlc...). Optional url/args, e.g. "
                    "open brave with a website. Whitelisted only.",
     "parameters": _obj({"app": {"type": "string"},
                         "args": {"type": "array", "items": {"type": "string"}}},
                        ["app"])},
    {"name": "web_query", "level": 1,
     "description": "Search the web (Bing/DuckDuckGo/Wikipedia). Returns titles, "
                    "urls and snippets. Use several focused queries.",
     "parameters": _obj({"queries": {"type": "array", "items": {"type": "string"}},
                         "max_results_per_query": {"type": "integer"}},
                        ["queries"])},
    {"name": "scrape_url", "level": 1,
     "description": "Fetch a web page and extract its readable text "
                    "(optionally JS-rendered with use_js=true). Use after "
                    "web_query to read a specific product/page.",
     "parameters": _obj({"url": {"type": "string"},
                         "use_js": {"type": "boolean"}}, ["url"])},
]


async def execute_tool(name: str, args: dict, ctx: ToolContext) -> dict:
    """Execute a tool with permission + policy enforcement. Never raises."""
    try:
        return await _execute(name, args, ctx)
    except PermissionDenied as e:
        permissions.audit(f"tool_denied:{name}", str(args)[:300], allowed=False,
                          level=2, session_id=ctx.session_id)
        return {"error": "permission_denied", "message": str(e), "tool": name}
    except Exception as e:
        log.exception("tool %s failed", name)
        return {"error": "execution_failed", "message": str(e)[:400], "tool": name}


async def _execute(name: str, args: dict, ctx: ToolContext) -> dict:
    ws = ctx.workspace
    sid = ctx.session_id

    def _req(key: str):
        """Fetch a required argument; missing args return a clean model-visible
        error instead of crashing the whole tool (models sometimes omit them)."""
        v = args.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError(f"missing required argument '{key}' for {name}")
        return v

    def _check_write(path: str = ""):
        if ws and path:
            permissions.check_access(path if path.startswith("/") or ":" in path
                                     else __import__("os").path.join(ws, path),
                                     need_write=True, session_id=sid)

    if name == "read_file":
        return filesystem.read_file(_abs(ws, _req("path")), sid)
    if name == "list_directory":
        return filesystem.list_directory(_abs(ws, _req("path")), sid)
    if name == "search_files":
        return filesystem.search_files(_abs(ws, _req("path")), _req("pattern"),
                                       content=args.get("content"), session_id=sid)
    if name == "write_file" or name == "create_file":
        r = filesystem.write_file(_abs(ws, _req("path")), args.get("content", ""), sid)
        return r
    if name == "edit_file":
        return _edit_file(ws, args, sid)
    if name == "create_directory":
        return filesystem.create_directory(_abs(ws, _req("path")), sid)
    if name == "delete_path":
        return filesystem.delete_path(_abs(ws, _req("path")),
                                      recursive=bool(args.get("recursive")),
                                      confirm=bool(args.get("confirm")), session_id=sid)
    if name == "move_path":
        return filesystem.move_path(_abs(ws, _req("source")), _abs(ws, _req("destination")),
                                    confirm=bool(args.get("confirm")), session_id=sid)
    if name == "copy_path":
        return filesystem.copy_path(_abs(ws, _req("source")), _abs(ws, _req("destination")),
                                    session_id=sid)
    if name == "execute_command":
        timeout = min(float(args.get("timeout_seconds", 120)), 600)
        r = await terminal_engine.execute(_req("command"), ws, timeout=timeout, session_id=sid)
        return r
    if name == "start_process":
        return await terminal_engine.start_process(_req("command"), ws, session_id=sid)
    if name == "read_process_output":
        return terminal_engine.read_output(int(args["process_id"]))
    if name == "stop_process":
        return await terminal_engine.stop_process(int(args["process_id"]), session_id=sid)
    if name == "install_dependency":
        return await _install_dependency(ws, args, sid)
    if name == "run_tests":
        return await _detect_and_run(ws, sid)
    if name == "build_project":
        cmd = args.get("command") or "npm run build"
        return await terminal_engine.execute(cmd, ws, timeout=300, session_id=sid)
    if name == "inspect_project":
        status = workspace.workspace_status(ws)
        status["tree"] = workspace.file_tree(ws, depth=2)["tree"]
        return status
    if name == "detect_os":
        from .os_backends import execution_backend
        return {"os": execution_backend.os_name, "info": execution_backend.system_info()}
    if name == "get_system_information":
        from .os_backends import execution_backend
        return execution_backend.system_info()
    if name == "check_installed_tools":
        import shutil
        return {t: bool(shutil.which(t)) for t in args.get("tools", [])}
    if name == "request_elevation":
        aid = permissions.request_approval("elevation", args.get("description", ""))
        return {"approval_id": aid,
                "status": "pending — the user must approve; nothing executed"}
    if name == "git_checkpoint":
        return workspace.checkpoint(ws, args.get("message", "NEURAL WEB checkpoint"))
    if name == "git_diff":
        return workspace.git_diff(ws)
    if name == "launch_app":
        from . import app_launcher
        return app_launcher.launch(_req("app"), args.get("args") or [], sid)
    if name == "web_query":
        queries = args.get("queries") or []
        if isinstance(queries, str):        # models sometimes send one string
            queries = [queries]
        queries = [str(q).strip() for q in queries if str(q).strip()][:6]
        if not queries:
            return {"error": "no_queries"}
        from ..search.search_manager import SearchManager
        results = await SearchManager().search(
            queries, max_results_per_query=min(int(args.get(
                "max_results_per_query", 6)), 10))
        return {"results": [{"title": r.title, "url": r.url,
                             "snippet": r.snippet} for r in results]}
    if name == "scrape_url":
        from ..scraping.engine import scrape_engine
        pc = await scrape_engine.scrape(_req("url"), session_id=sid,
                                        use_js=bool(args.get("use_js")))
        text = pc.clean_text or pc.raw_text or ""
        return {"url": pc.url, "status": pc.status, "title": pc.title,
                "text": text[:12000], "word_count": pc.word_count,
                "links": pc.links[:40]}
    return {"error": f"unknown tool: {name}"}


def _abs(ws: str, path: str) -> str:
    import os
    p = path.strip()
    if p.startswith("/") or ":" in p[:3]:
        return p
    return os.path.join(ws or ".", p)


def _edit_file(ws: str, args: dict, sid: str) -> dict:
    path = args.get("path")
    if not path:
        return {"error": "missing required argument 'path' for edit_file"}
    path = _abs(ws, path)
    data = filesystem.read_file(path, sid)
    content = data["content"]
    old, new = args["old_string"], args["new_string"]
    if old not in content:
        return {"error": "old_string not found", "path": path}
    occurrences = content.count(old)
    if occurrences > 1 and not args.get("replace_all"):
        return {"error": f"old_string occurs {occurrences} times; pass replace_all or "
                         "include more context", "path": path}
    updated = content.replace(old, new) if args.get("replace_all") \
        else content.replace(old, new, 1)
    filesystem.write_file(path, updated, sid)
    return {"path": path, "edited": True,
            "replacements": occurrences if args.get("replace_all") else 1}


async def _install_dependency(ws: str, args: dict, sid: str) -> dict:
    manager = args.get("manager", "pip")
    packages = [p for p in args.get("packages", []) if re_pkg_ok(p)]
    if not packages:
        return {"error": "no valid package names"}
    if manager == "pip":
        # Project-local: prefer workspace venv; fall back to --user (never --system)
        venv_pip = __import__("os").path.join(ws, ".venv", "bin", "pip")
        import os
        if os.path.exists(venv_pip):
            cmd = f"{venv_pip} install {' '.join(packages)}"
        else:
            venv_root = os.path.join(ws, ".venv")
            cmd = f"python3 -m venv {venv_root} && {venv_pip} install {' '.join(packages)}"
    elif manager in ("npm", "yarn", "pnpm"):
        cmd = f"{manager} install {' '.join(packages)}"   # project-local by default
    else:
        return {"error": f"unsupported manager {manager}"}
    r = await terminal_engine.execute(cmd, ws, timeout=600, session_id=sid)
    return r | {"installed": packages, "manager": manager}


def re_pkg_ok(pkg: str) -> bool:
    import re
    return bool(re.match(r"^[A-Za-z0-9_@/.\-~=+]+$", pkg)) and ".." not in pkg


async def _detect_and_run(ws: str, sid: str) -> dict:
    import os
    if os.path.exists(os.path.join(ws, "package.json")):
        return await terminal_engine.execute("npm test --if-present", ws, 600, sid)
    if os.path.exists(os.path.join(ws, "pytest.ini")) or \
            os.path.exists(os.path.join(ws, "tests")) or \
            any(f.endswith("_test.py") or f.startswith("test_")
                for f in os.listdir(ws) if f.endswith(".py")):
        return await terminal_engine.execute(
            "python3 -m pytest -q 2>&1 | tail -40", ws, 600, sid)
    return {"note": "no tests detected", "ran": False}


def tool_schemas() -> list[dict]:
    """OpenAI/Groq function-calling schemas derived from the registry."""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"],
        "parameters": t["parameters"]}} for t in TOOLS]
