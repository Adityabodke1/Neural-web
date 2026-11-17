"""Provider & model manager: runtime CRUD, connection testing, model routing.

All configuration lives in the database (providers + ai_models tables), added
by the user via CLI or dashboard. Nothing is hardcoded except the built-in
provider definitions (which carry no keys and no default model IDs).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from ..database.database import db, init_db
from .adapter import AdapterError, build_adapter, parse_json_loose
from .registry import BUILTIN_BY_ID
from . import secrets_store

log = logging.getLogger("neuralweb.providers")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- providers

def bootstrap_from_env() -> None:
    """Ensure built-in providers exist and register any keys present in .env."""
    init_db()
    for pid, meta in BUILTIN_BY_ID.items():
        base_url = meta["base_url"]
        with db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO providers (id, name, kind, base_url, enabled,"
                " key_source, created_at) VALUES (?,?,?,?,1,'none',?)",
                (pid, meta["name"], meta["kind"], base_url, utcnow()))
        env_key = secrets_store.key_from_env(pid)
        if env_key:
            with db() as conn:
                conn.execute(
                    "UPDATE providers SET key_source='env' WHERE id=? AND key_source='none'",
                    (pid,))
    # Bootstrap a default model from env vars (user-configurable, nothing hardcoded
    # in code: these come from settings → .env).
    from ..config import settings as cfg
    if cfg.has_groq() and not _any_model():
        add_model("groq", cfg.GROQ_MODEL, label=cfg.GROQ_MODEL, is_default=True)
        add_model("groq", cfg.GROQ_FALLBACK_MODEL, label=cfg.GROQ_FALLBACK_MODEL)


def _any_model() -> bool:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM ai_models").fetchone()["c"] > 0


def list_providers() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM providers ORDER BY id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        key = _provider_key(d)
        d["api_key_present"] = bool(key)
        d["api_key_masked"] = secrets_store.mask(key)
        d.pop("_db_row", None)
        out.append(d)
    return out


def get_provider(provider_id: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    key = _provider_key(d)
    d["api_key_present"] = bool(key)
    d["api_key_masked"] = secrets_store.mask(key)
    return d


def update_provider(provider_id: str, *, base_url: Optional[str] = None,
                    enabled: Optional[bool] = None, api_key: Optional[str] = None,
                    name: Optional[str] = None) -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not row:
            raise ValueError(f"unknown provider {provider_id}")
        if name is not None:
            conn.execute("UPDATE providers SET name=? WHERE id=?", (name, provider_id))
        if base_url is not None:
            conn.execute("UPDATE providers SET base_url=? WHERE id=?", (base_url, provider_id))
        if enabled is not None:
            conn.execute("UPDATE providers SET enabled=? WHERE id=?", (int(enabled), provider_id))
    if api_key:
        source = secrets_store.store_key(provider_id, api_key)
        with db() as conn:
            conn.execute("UPDATE providers SET key_source=? WHERE id=?", (source, provider_id))
    return get_provider(provider_id)


def remove_provider_key(provider_id: str) -> None:
    secrets_store.delete_key(provider_id)
    with db() as conn:
        conn.execute("UPDATE providers SET key_source='none' WHERE id=?", (provider_id,))


def _provider_key(provider_row: dict) -> str:
    if provider_row.get("key_source") == "env":
        return secrets_store.key_from_env(provider_row["id"])
    return secrets_store.get_key(provider_row["id"], db_value="")


def _adapter_for(provider_id: str):
    p = get_provider(provider_id)
    if not p:
        raise AdapterError(f"unknown provider {provider_id}")
    if not p.get("enabled"):
        raise AdapterError(f"provider {provider_id} is disabled")
    key = _provider_key(p)
    if not key:
        raise AdapterError(f"provider {provider_id} has no API key configured")
    return build_adapter(p, key)


# ---------------------------------------------------------------- models

def add_model(provider_id: str, model_id: str, label: str = "",
              context_tokens: int | None = None, is_default: bool = False,
              task: str | None = None, source: str = "manual") -> dict:
    if not get_provider(provider_id):
        raise ValueError(f"unknown provider {provider_id}")
    # Enrich with the official context window from the provider's live catalog
    # when the caller didn't specify one (best-effort; stays None if the
    # catalog is unavailable — never guessed).
    if context_tokens is None:
        try:
            for entry in fetch_provider_catalog(provider_id):
                if entry.get("model_id") == model_id and entry.get("context_tokens"):
                    context_tokens = int(entry["context_tokens"])
                    break
        except Exception:
            pass  # catalog fetch failed; context stays unknown, not invented
    ref = f"{provider_id}/{model_id}"
    if is_default:
        with db() as conn:
            conn.execute("UPDATE ai_models SET is_default=0")
    with db() as conn:
        conn.execute(
            "INSERT INTO ai_models (id, provider_id, model_id, label, enabled, is_default,"
            " task, context_tokens, source, created_at) VALUES (?,?,?,?,1,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET label=excluded.label, enabled=1,"
            " is_default=excluded.is_default, task=excluded.task,"
            " context_tokens=COALESCE(excluded.context_tokens, ai_models.context_tokens)",
            (ref, provider_id, model_id, label or model_id, int(is_default), task,
             context_tokens, source, utcnow()))
    return get_model(ref)


def list_models(provider_id: str | None = None) -> list[dict]:
    from ..quotas.tracker import quota_tracker
    with db() as conn:
        if provider_id:
            rows = conn.execute(
                "SELECT * FROM ai_models WHERE provider_id=? ORDER BY is_default DESC, id",
                (provider_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ai_models ORDER BY is_default DESC, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        q = quota_tracker.get_quota(d["id"])
        d["quota"] = q
        out.append(d)
    return out


def get_model(ref: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM ai_models WHERE id=?", (ref,)).fetchone()
    return dict(row) if row else None


def remove_model(ref: str) -> bool:
    with db() as conn:
        cur = conn.execute("DELETE FROM ai_models WHERE id=?", (ref,))
        return cur.rowcount > 0


def set_default_model(ref: str) -> None:
    if not get_model(ref):
        raise ValueError(f"unknown model {ref}")
    with db() as conn:
        conn.execute("UPDATE ai_models SET is_default=0")
        conn.execute("UPDATE ai_models SET is_default=1, enabled=1 WHERE id=?", (ref,))


def set_model_enabled(ref: str, enabled: bool) -> None:
    with db() as conn:
        conn.execute("UPDATE ai_models SET enabled=? WHERE id=?", (int(enabled), ref))


def set_task_model(ref: str, task: str | None) -> None:
    with db() as conn:
        conn.execute("UPDATE ai_models SET task=? WHERE id=?", (task, ref))


def resolve_model(model_spec: str = "", task: str = "") -> Optional[dict]:
    """Resolve a model reference.

    model_spec:
      - ''            → task-specific model, else default, else first enabled
      - 'auto'        → default model (best available)
      - 'provider/model' → exact ref
      - 'model-id'    → match any provider's model_id
    """
    if model_spec and model_spec != "auto":
        if "/" in model_spec:
            m = get_model(model_spec)
            if m and m.get("enabled"):
                return m
            raise ValueError(f"model {model_spec} is not configured/enabled")
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM ai_models WHERE model_id=? AND enabled=1", (model_spec,)
            ).fetchone()
        if row:
            return dict(row)
        raise ValueError(f"model {model_spec} is not configured")
    if task:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM ai_models WHERE task=? AND enabled=1", (task,)).fetchone()
        if row:
            return dict(row)
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM ai_models WHERE is_default=1 AND enabled=1").fetchone()
        if not row:
            row = conn.execute(
                "SELECT * FROM ai_models WHERE enabled=1 ORDER BY id LIMIT 1").fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- testing

async def test_connection(provider_id: str, model_id: str = "") -> dict:
    """Real connection test: sends a tiny prompt. Never fakes success."""
    try:
        adapter = _adapter_for(provider_id)
    except AdapterError as e:
        return {"ok": False, "error": str(e)}
    mid = model_id or ""
    if not mid:
        try:
            models = adapter.list_models()
        except AdapterError as e:
            return {"ok": False, "error": str(e)}
        # test with a real CHAT model — guard/TTS/embedding models produce
        # meaningless replies and would make a working key look broken
        import re as _re
        skip = _re.compile(
            r"safeguard|prompt-guard|guard-2|whisper|orpheus|tts|embed|"
            r"moderation|distil-whisper|playai", _re.IGNORECASE)
        chat_models = [m["model_id"] for m in models if not skip.search(m["model_id"])]
        mid = chat_models[0] if chat_models else \
            (models[0]["model_id"] if models else "")
    if not mid:
        return {"ok": False, "error": "no model specified and catalog listing returned none"}
    try:
        result = adapter.chat(mid, [
            {"role": "user", "content": "Reply with exactly: OK"}],
            temperature=0.0, max_tokens=512)
        ok = "ok" in (result["content"] or "").lower() or \
            bool((result["content"] or "").strip()) or \
            (result["usage"]["completion_tokens"] > 0 and not result.get("tool_calls"))
        return {"ok": ok, "model": mid, "reply": (result["content"] or "")[:50],
                "latency_ms": result["latency_ms"],
                "usage": result["usage"], "model_ref": result["model_ref"]}
    except AdapterError as e:
        return {"ok": False, "model": mid, "error": str(e)}


def fetch_provider_catalog(provider_id: str) -> list[dict]:
    """List models directly from the provider's API."""
    adapter = _adapter_for(provider_id)
    return adapter.list_models()


def import_catalog_models(provider_id: str, prefixes: list[str] = []) -> int:
    """Import provider catalog entries matching prefixes ('' = all).
    Skips known non-chat models (guardrails, TTS, ASR, embeddings) — they
    would only pollute the failover chain."""
    import re as _re
    skip = _re.compile(
        r"safeguard|prompt-guard|guard-2|whisper|orpheus|tts|embed|moderation|"
        r"distil-whisper|playai", _re.IGNORECASE)
    models = fetch_provider_catalog(provider_id)
    n = 0
    for m in models:
        mid = m["model_id"]
        if prefixes and not any(mid.startswith(p) for p in prefixes):
            continue
        if skip.search(mid):
            continue
        add_model(provider_id, mid, label=m.get("label") or mid,
                  context_tokens=m.get("context_tokens"), source="catalog")
        n += 1
    return n


# ---------------------------------------------------------------- free models

def _openrouter_free_models() -> list[dict]:
    """OpenRouter publishes per-model pricing on its PUBLIC /models endpoint.
    prompt+completion == "0" is authoritative zero-cost API access."""
    import httpx
    try:
        resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        log.warning("openrouter free-model fetch failed: %s", e)
        return []
    out = []
    for m in data:
        pricing = m.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt", "1") or "1")
            completion = float(pricing.get("completion", "1") or "1")
        except (TypeError, ValueError):
            continue
        if prompt == 0.0 and completion == 0.0:
            out.append({"provider": "openrouter", "model_id": m.get("id", ""),
                        "context_tokens": m.get("context_length"),
                        "source": "pricing=0/0 (OpenRouter API)"})
    return out


def list_free_models() -> dict:
    """Models with officially verified zero-cost API usage. Auto-refreshed by
    a live fetch on every call — never cached, never guessed.
    Excluded: paid models, trial-credit-only models, and anything whose
    pricing/free eligibility is unknown. Free CONTEXT windows count for
    nothing here — only zero-cost API usage."""
    out: list[dict] = []
    excluded_providers: list[str] = []
    for p in list_providers():
        meta = BUILTIN_BY_ID.get(p["id"], {})
        pid = p["id"]
        if meta.get("pricing_metadata"):
            out.extend(_openrouter_free_models())
        elif meta.get("free_tier") and p["api_key_present"]:
            try:
                models = fetch_provider_catalog(pid)
            except Exception:
                excluded_providers.append(pid)
                continue
            import re as _re
            skip = _re.compile(
                r"safeguard|prompt-guard|guard-2|whisper|orpheus|tts|embed|"
                r"moderation|distil-whisper|playai", _re.IGNORECASE)
            for m in models:
                if skip.search(m["model_id"]):
                    continue
                out.append({"provider": pid, "model_id": m["model_id"],
                            "context_tokens": m.get("context_tokens"),
                            "source": f"{meta.get('free_tier_source', '')} "
                                      f"(provider free tier, rate-limited)"})
        else:
            # no pricing metadata and no verified free tier → hidden entirely
            if p["api_key_present"]:
                excluded_providers.append(pid)
    return {"free": out, "excluded_unknown": excluded_providers}


# bootstrap on import
try:
    bootstrap_from_env()
except Exception as e:  # pragma: no cover
    log.warning("provider bootstrap failed: %s", e)


provider_manager = type("ProviderManager", (), {})  # namespace alias
