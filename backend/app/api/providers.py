"""Providers / models / quotas / benchmarks API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..benchmarks import harness
from ..providers import manager as pm
from ..quotas.tracker import quota_tracker

router = APIRouter(prefix="/api", tags=["providers"])


# ---------------- providers ----------------

class ProviderUpdate(BaseModel):
    base_url: str | None = None
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=400)
    name: str | None = None


@router.get("/providers")
async def list_providers():
    return pm.list_providers()


@router.patch("/providers/{provider_id}")
async def update_provider(provider_id: str, req: ProviderUpdate):
    try:
        p = pm.update_provider(provider_id, base_url=req.base_url, enabled=req.enabled,
                               api_key=req.api_key, name=req.name)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return p


@router.delete("/providers/{provider_id}/key")
async def delete_key(provider_id: str):
    pm.remove_provider_key(provider_id)
    return {"deleted": True}


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: str, model_id: str = ""):
    return await pm.test_connection(provider_id, model_id)


@router.get("/providers/{provider_id}/catalog")
async def catalog(provider_id: str):
    try:
        return {"models": pm.fetch_provider_catalog(provider_id)}
    except Exception as e:
        raise HTTPException(502, str(e))


# ---------------- models ----------------

class ModelAdd(BaseModel):
    provider_id: str
    model_id: str = Field(max_length=200)
    label: str = ""
    context_tokens: int | None = None
    is_default: bool = False
    task: str | None = None


@router.get("/models")
async def list_models(provider: str | None = None):
    return pm.list_models(provider)


@router.post("/models")
async def add_model(req: ModelAdd):
    try:
        return pm.add_model(req.provider_id, req.model_id, req.label,
                            req.context_tokens, req.is_default, req.task)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/models/{ref:path}")
async def remove_model(ref: str):
    return {"removed": pm.remove_model(ref)}


@router.post("/models/{ref:path}/default")
async def set_default(ref: str):
    try:
        pm.set_default_model(ref)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"default": ref}


@router.post("/models/{ref:path}/task")
async def set_task(ref: str, task: str | None = None):
    pm.set_task_model(ref, task)
    return {"model": ref, "task": task}


# ---------------- quotas ----------------

@router.get("/usage")
async def usage(provider: str | None = None, model: str | None = None):
    if model:
        return quota_tracker.get_quota(model)
    return quota_tracker.overview(provider)


@router.post("/usage/set-limit")
async def set_limit(model_ref: str, limit_tokens: int,
                    period: str = "manual", reset_at: str | None = None):
    quota_tracker.set_manual_limit(model_ref, limit_tokens, period, reset_at)
    return quota_tracker.get_quota(model_ref)


@router.delete("/usage/set-limit")
async def clear_limit(model_ref: str):
    quota_tracker.clear_limit(model_ref)
    return {"cleared": model_ref}


@router.post("/usage/refresh")
async def refresh(provider: str = "openrouter"):
    return await quota_tracker.refresh_provider_quota(provider)


# ---------------- benchmarks ----------------

class BenchRequest(BaseModel):
    model: str = "auto"
    capabilities: list[str] | None = None


@router.post("/benchmarks/run")
async def run_benchmarks(req: BenchRequest):
    return await harness.run_benchmark(req.model, req.capabilities)


@router.get("/benchmarks")
async def benchmarks(model: str | None = None):
    return harness.get_benchmarks(model)


@router.get("/benchmarks/{ref:path}/summary")
async def bench_summary(ref: str):
    return harness.summarize(ref)
