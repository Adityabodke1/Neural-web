"""Settings & status API (kill switches, sandbox, provider status)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import settings
from ..database import repository as repo
from ..memory.embeddings import embedder
from ..providers import manager as pm
from ..quotas.tracker import quota_tracker
from ..search.search_manager import search_manager
from ..scraping.engine import scrape_engine
from ..computer import terminal as computer_terminal
from ..computer.sandbox import sandbox_status

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return {
        "ai": ai_status(),
        "search": {"engine": search_manager.engine_name,
                   "tavily_configured": settings.has_tavily()},
        "scraping": {"engine": scrape_engine.name,
                     "firecrawl_configured": settings.has_firecrawl(),
                     "max_concurrent": settings.MAX_CONCURRENT_SCRAPES},
        "memory": {"embedder": embedder.name},
        "sandbox": sandbox_status(),
        "switches": computer_terminal.switch_status(),
        "database": {"path": str(settings.db_path)},
    }


def ai_status() -> dict:
    models = pm.list_models()
    active = None
    try:
        active = pm.resolve_model("auto")
    except Exception:
        pass
    return {
        "active_model": active["id"] if active else None,
        "models_configured": len(models),
        "quota_overview": quota_tracker.overview()["total"],
    }


class SwitchUpdate(BaseModel):
    name: str
    value: bool


@router.post("/switches")
async def set_switch(req: SwitchUpdate):
    if req.name not in computer_terminal.SWITCHES:
        raise HTTPException(422, f"unknown switch {req.name}")
    repo.set_setting(req.name, "true" if req.value else "false")
    return computer_terminal.switch_status()


@router.post("/emergency-stop")
async def emergency_stop():
    return computer_terminal.emergency_stop()


@router.post("/emergency-clear")
async def emergency_clear():
    return computer_terminal.clear_emergency_stop()
