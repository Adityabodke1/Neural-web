"""NEURAL WEB — FastAPI application entrypoint."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import computer, history, providers, research, scraping, settings
from .database.database import init_db
from .providers import manager as pm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="NEURAL WEB", version="2.0.0",
              description="Autonomous AI Web Research, Scraping & Development Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173",
                   "http://localhost:4173", "http://127.0.0.1:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

for r in (research.router, scraping.router, history.router,
          providers.router, settings.router, computer.router):
    app.include_router(r)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    pm.bootstrap_from_env()


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "NEURAL WEB", "version": "2.0.0"}


# ---- serve the built dashboard (frontend/dist) when available ----
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        if (candidate := _dist / path.lstrip("/")).is_file() and ".." not in path:
            return FileResponse(candidate)
        return FileResponse(_dist / "index.html")
