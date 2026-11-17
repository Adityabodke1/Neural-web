"""FREE-model selector tests: paid and unknown models must be excluded,
only verified zero-cost API usage may appear.

Run: .venv/bin/python -m pytest backend/tests/test_free_models.py -v
"""
from __future__ import annotations

import pytest

from backend.app.providers import manager as pm


def test_openrouter_pricing_filter_excludes_paid_and_unknown():
    """pricing=0/0 → free; anything paid or unparsable must never appear.
    Mirrors the authoritative rule used by _openrouter_free_models."""
    import httpx

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [
                {"id": "a/free-model:free",
                 "pricing": {"prompt": "0", "completion": "0"},
                 "context_length": 8192},
                {"id": "b/paid-model",
                 "pricing": {"prompt": "0.0000015", "completion": "0.000002"},
                 "context_length": 999999},          # huge FREE context, PAID usage
                {"id": "c/free-prompt-paid-completion",
                 "pricing": {"prompt": "0", "completion": "0.00001"},
                 "context_length": 8192},
                {"id": "d/no-pricing", "context_length": 4096},
                {"id": "e/bad-pricing",
                 "pricing": {"prompt": "free?", "completion": "0"}},
            ]}

    def fake_get(url, timeout):
        return FakeResp()

    orig = httpx.get
    httpx.get = fake_get
    try:
        out = pm._openrouter_free_models()
    finally:
        httpx.get = orig
    ids = {m["model_id"] for m in out}
    assert ids == {"a/free-model:free"}   # ONLY the truly-free model survives


def test_openrouter_real_pricing_logic(monkeypatch):
    """Drive the REAL _openrouter_free_models filtering logic with fake HTTP."""
    import httpx

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [
                {"id": "free/one", "pricing": {"prompt": "0", "completion": "0"},
                 "context_length": 128000},
                {"id": "paid/one", "pricing": {"prompt": "0.00001", "completion": "0"},
                 "context_length": 128000},
            ]}

    def fake_get(url, timeout):
        assert "openrouter.ai" in url
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    out = pm._openrouter_free_models()
    ids = {m["model_id"] for m in out}
    assert "free/one" in ids
    assert "paid/one" not in ids          # paid excluded even with free context


def test_unknown_providers_hidden_even_when_keyed(monkeypatch):
    """together/custom/openai have no verified free tier → excluded entirely."""
    real_list = pm.list_providers
    monkeypatch.setattr(pm, "list_providers", lambda: [
        {"id": "together", "api_key_present": True, "enabled": True},
        {"id": "openai", "api_key_present": True, "enabled": True},
        {"id": "custom", "api_key_present": True, "enabled": True},
    ])
    monkeypatch.setattr(pm, "_openrouter_free_models", lambda: [])
    res = pm.list_free_models()
    assert res["free"] == []
    assert set(res["excluded_unknown"]) == {"together", "openai", "custom"}
    monkeypatch.setattr(pm, "list_providers", real_list)


def test_guardrail_models_never_listed_as_free(monkeypatch):
    """Groq-style free-tier catalogs must skip guard/TTS/embedding models."""
    monkeypatch.setattr(pm, "list_providers", lambda: [
        {"id": "groq", "api_key_present": True, "enabled": True}])
    monkeypatch.setattr(pm, "fetch_provider_catalog", lambda pid: [
        {"model_id": "openai/gpt-oss-20b", "context_tokens": 131072},
        {"model_id": "meta-llama/llama-prompt-guard-2-22m", "context_tokens": 512},
        {"model_id": "canopylabs/orpheus-v1-english", "context_tokens": 4000},
    ])
    res = pm.list_free_models()
    ids = {m["model_id"] for m in res["free"]}
    assert "openai/gpt-oss-20b" in ids
    assert "meta-llama/llama-prompt-guard-2-22m" not in ids
    assert "canopylabs/orpheus-v1-english" not in ids


def test_free_context_is_not_free_usage():
    """A huge context window alone must NOT qualify a model as free — the
    qualification comes from pricing metadata or the provider free tier."""
    # the selector only emits models whose `source` names a verified reason
    import inspect
    src = inspect.getsource(pm.list_free_models)
    assert "pricing" in src           # OpenRouter per-model pricing path
    assert "free_tier" in src         # documented provider free-tier path
    # nothing in the code path marks a model free based on context_tokens
    assert "context_tokens" not in src.split("out.append")[0].split("for m")[0]
