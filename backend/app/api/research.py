"""Research API: start/stop/stream sessions, fetch results, export."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..agent import research_agent
from ..agent.task_manager import registry
from ..database import repository as repo
from ..exports.exporters import export_session

log = logging.getLogger("neuralweb.api.research")

router = APIRouter(prefix="/api/research", tags=["research"])


class ResearchStart(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    depth: str = Field(default="standard", pattern="^(quick|standard|deep)$")
    max_pages: int = Field(default=0, ge=0, le=60)
    time_limit_minutes: int = Field(default=0, ge=0, le=60)
    target_fields: list[str] = Field(default_factory=list, max_length=20)
    include_domains: list[str] = Field(default_factory=list, max_length=10)
    exclude_domains: list[str] = Field(default_factory=list, max_length=10)
    model: str = Field(default="auto", max_length=200)


@router.post("/start")
async def start(req: ResearchStart):
    if not req.prompt.strip():
        raise HTTPException(422, "prompt required")
    params = {
        "max_pages": req.max_pages, "time_limit_minutes": req.time_limit_minutes,
        "target_fields": req.target_fields, "include_domains": req.include_domains,
        "exclude_domains": req.exclude_domains, "model": req.model,
    }
    session_id = repo.create_session(req.prompt.strip(), req.depth, params)
    await research_agent.start_research(session_id)
    return {"session_id": session_id, "status": "queued"}


@router.post("/resume/{session_id}")
async def resume(session_id: str):
    if not repo.get_session(session_id):
        raise HTTPException(404, "session not found")
    await research_agent.start_research(session_id, resume=True)
    return {"session_id": session_id, "status": "resumed"}


@router.get("/sessions")
async def sessions():
    return repo.list_sessions()


@router.get("/{session_id}")
async def detail(session_id: str):
    s = repo.get_session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    s["params"] = json.loads(s.pop("params_json", "{}"))
    s["plan"] = json.loads(s.get("plan_json") or "null")
    s.pop("plan_json", None)
    s["report_json"] = json.loads(s.get("report_json") or "null")
    return {
        "session": s,
        "queries": repo.list_queries(session_id),
        "urls": repo.list_urls(session_id),
        "pages": repo.list_pages(session_id),
        "records": repo.list_records(session_id),
        "progress": (await registry.get(session_id, create=False)).snapshot()
        if await registry.get(session_id, create=False) else None,
    }


@router.get("/{session_id}/events")
async def events(session_id: str):
    tm = await registry.get(session_id, create=False)
    if tm is None:
        raise HTTPException(404, "no live task for this session")

    async def gen():
        q = tm.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'hello', 'payload': tm.snapshot()})}\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(evt, ensure_ascii=False, default=str)}\n\n"
                    if evt.get("type") == "done":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            tm.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.post("/{session_id}/stop")
async def stop(session_id: str):
    tm = await registry.get(session_id, create=False)
    if tm is None:
        raise HTTPException(404, "no live task")
    tm.request_stop()
    return {"stopped": True}


@router.get("/{session_id}/export")
async def export(session_id: str, format: str = "json"):
    try:
        out = export_session(session_id, format)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return FileResponse(out["path"], filename=Path(out["path"]).name)


@router.get("/stats/dashboard")
async def dashboard():
    return repo.dashboard_stats() | {"usage": repo.usage_summary()}
