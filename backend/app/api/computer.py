"""Computer agent API: folders, files, terminal, coding tasks, approvals, browser."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..computer import browser_agent, coding_agent, permissions, tools, workspace
from ..computer.terminal import terminal_engine
from ..computer.tools import ToolContext

router = APIRouter(prefix="/api/computer", tags=["computer"])


# ---------------- folders & permissions ----------------

class FolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    label: str = ""


@router.get("/folders")
async def list_folders():
    return {"folders": permissions.list_folders(),
            "pending_approvals": permissions.list_approvals()}


@router.post("/folders")
async def add_folder(req: FolderRequest):
    try:
        return permissions.authorize_folder(req.path, req.label)
    except permissions.PermissionDenied as e:
        raise HTTPException(422, str(e))


@router.delete("/folders")
async def revoke_folder(path: str):
    return {"revoked": permissions.revoke_folder(path)}


@router.get("/approvals")
async def approvals(status: str = "pending"):
    return permissions.list_approvals(status)


@router.post("/approvals/{approval_id}/decide")
async def decide(approval_id: int, approve: bool):
    ok = permissions.decide_approval(approval_id, approve)
    if not ok:
        raise HTTPException(404, "approval not found or already decided")
    return {"decided": True}


# ---------------- files ----------------

class FileRequest(BaseModel):
    path: str
    content: str = ""
    recursive: bool = False
    confirm: bool = False
    destination: str = ""
    source: str = ""
    pattern: str = "*"


@router.post("/files/read")
async def read_file(req: FileRequest):
    try:
        return tools.filesystem.read_file(req.path)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.post("/files/write")
async def write_file(req: FileRequest):
    try:
        return tools.filesystem.write_file(req.path, req.content)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.post("/files/delete")
async def delete_file(req: FileRequest):
    try:
        return tools.filesystem.delete_path(req.path, req.recursive, req.confirm)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.post("/files/list")
async def list_dir(req: FileRequest):
    try:
        return tools.filesystem.list_directory(req.path)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


# ---------------- workspace ----------------

@router.get("/workspace/status")
async def ws_status(path: str):
    try:
        return workspace.workspace_status(path)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.get("/workspace/tree")
async def ws_tree(path: str, depth: int = 3):
    try:
        return workspace.file_tree(path, depth=depth)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.post("/workspace/checkpoint")
async def ws_checkpoint(path: str, message: str = "NEURAL WEB checkpoint"):
    try:
        return workspace.checkpoint(path, message)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.post("/workspace/restore")
async def ws_restore(path: str, ref: str = "HEAD~1"):
    try:
        return workspace.restore_checkpoint(path, ref)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


# ---------------- terminal ----------------

class CommandRequest(BaseModel):
    workspace: str
    command: str = Field(min_length=1, max_length=4000)
    timeout_seconds: int = Field(default=120, ge=1, le=600)


@router.post("/terminal/execute")
async def execute(req: CommandRequest):
    try:
        return await terminal_engine.execute(
            req.command, req.workspace, timeout=float(req.timeout_seconds))
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.get("/terminal/processes")
async def processes():
    return {"processes": terminal_engine.list_processes()}


@router.post("/terminal/stop-all")
async def stop_all():
    return computer_terminal.emergency_stop()


# ---------------- coding agent ----------------

class TaskRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=8000)
    workspace: str = Field(min_length=1)
    max_steps: int = Field(default=40, ge=1, le=120)
    model: str = "auto"


@router.post("/tasks/start")
async def start_task(req: TaskRequest):
    try:
        return await coding_agent.start_coding_task(
            req.prompt, req.workspace, req.max_steps, req.model)
    except permissions.PermissionDenied as e:
        raise HTTPException(403, str(e))


@router.get("/tasks")
async def tasks(limit: int = 50):
    return {"tasks": coding_agent.list_coding_tasks(limit)}


@router.get("/tasks/{task_id}")
async def task_detail(task_id: int):
    from ..agent.task_manager import registry
    tm = await registry.get(f"code-{task_id}", create=False)
    tasks = [t for t in coding_agent.list_coding_tasks() if t["id"] == task_id]
    if not tasks:
        raise HTTPException(404, "task not found")
    out = tasks[0]
    if tm:
        out["progress"] = tm.snapshot()
    return out


# ---------------- browser agent ----------------

class BrowserRequest(BaseModel):
    url: str
    selector: str = ""
    fields: list[dict] = Field(default_factory=list)
    submit: bool = False


@router.post("/browser/open")
async def browser_open(req: BrowserRequest):
    try:
        return await browser_agent.open_url(req.url)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/browser/extract")
async def browser_extract(req: BrowserRequest):
    try:
        return await browser_agent.extract_page(req.url)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/browser/screenshot")
async def browser_shot(req: BrowserRequest):
    try:
        return await browser_agent.take_screenshot(req.url)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/browser/console")
async def browser_console(req: BrowserRequest):
    try:
        return await browser_agent.read_console(req.url)
    except Exception as e:
        raise HTTPException(502, str(e))
