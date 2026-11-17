"""History, memory search, audit, sources API."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from ..database import repository as repo
from ..memory.vector_store import vector_store

router = APIRouter(prefix="/api", tags=["history"])


@router.get("/history")
async def history(limit: int = 50):
    sessions = repo.list_sessions(limit)
    return {"sessions": sessions}


@router.delete("/history/{session_id}")
async def delete_session(session_id: str):
    with repo.db() as conn:
        for table in ("records", "pages", "urls", "queries", "vectors"):
            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (session_id,))
        cur = conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
    return {"deleted": cur.rowcount > 0}


@router.get("/memory/search")
async def memory_search(q: str, top_k: int = 8):
    return {"query": q, "results": vector_store.search(q, top_k=top_k)}


@router.get("/sources")
async def sources(session_id: str | None = None, domain: str | None = None,
                  limit: int = 200):
    """Source transparency listing across sessions."""
    sql = ("SELECT p.*, s.prompt AS session_prompt FROM pages p "
           "LEFT JOIN sessions s ON s.id=p.session_id WHERE 1=1")
    args: list = []
    if session_id:
        sql += " AND p.session_id=?"
        args.append(session_id)
    if domain:
        sql += " AND p.domain LIKE ?"
        args.append(f"%{domain}%")
    sql += " ORDER BY p.id DESC LIMIT ?"
    args.append(limit)
    with repo.db() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
        d.pop("raw_text", None)
        d.pop("clean_text", None)
        out.append(d)
    return {"sources": out}


@router.get("/records/{record_id}")
async def record_detail(record_id: int):
    with repo.db() as conn:
        row = conn.execute(
            "SELECT r.*, s.prompt AS session_prompt FROM records r "
            "LEFT JOIN sessions s ON s.id=r.session_id WHERE r.id=?",
            (record_id,)).fetchone()
    if not row:
        raise HTTPException(404, "record not found")
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json"))
    page = repo.get_page(d["session_id"], d["url"]) if d["url"] else None
    d["page_evidence_available"] = bool(page and page.get("clean_text"))
    return d


@router.get("/audit")
async def audit_log(limit: int = 200, level: int | None = None):
    from ..computer import permissions
    rows = permissions.list_audit(limit)
    if level:
        rows = [r for r in rows if r["level"] >= level]
    return {"entries": rows}


@router.get("/stats/dashboard")
async def dashboard():
    from ..computer.sandbox import sandbox_status
    return repo.dashboard_stats() | {"usage": repo.usage_summary(),
                                     "sandbox": sandbox_status()}
