"""Built-in provider registry. Model names are NEVER hardcoded as defaults —
the user always picks/enters model IDs (except the Groq bootstrap from .env)."""
from __future__ import annotations

BUILTIN_PROVIDERS: list[dict] = [
    {
        "id": "groq",
        "name": "Groq",
        "kind": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "keys_url": "https://console.groq.com/keys",
        "quota_api": None,
        "notes": "OpenAI-compatible endpoint; free tier rate-limited.",
        # Officially documented free tier: every served chat model is usable
        # at zero cost with rate limits (console.groq.com/docs/rate-limits).
        "free_tier": True,
        "free_tier_source": "https://console.groq.com/docs/rate-limits",
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "kind": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "keys_url": "https://aistudio.google.com/app/apikey",
        "quota_api": None,
        "notes": "Native Gemini REST API.",
        # Google AI Studio API keys have an officially documented free tier
        # (free-of-charge usage with rate limits) for all models it serves.
        "free_tier": True,
        "free_tier_source": "https://ai.google.dev/pricing",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "kind": "openai_compat",
        "base_url": "https://api.openai.com/v1",
        "keys_url": "https://platform.openai.com/api-keys",
        "quota_api": None,
        "notes": "OpenAI-compatible.",
        "free_tier": False,   # paid per token; trial credits are NOT free usage
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "kind": "openai_compat",
        "base_url": "https://openrouter.ai/api/v1",
        "keys_url": "https://openrouter.ai/keys",
        "quota_api": "https://openrouter.ai/api/v1/auth/key",
        "notes": "Multi-provider gateway; exposes remaining credits via auth/key.",
        # Free/paid is decided PER MODEL from the catalog's pricing metadata
        # (prompt/completion == "0"), fetched live — no blanket claim.
        "free_tier": False,
        "pricing_metadata": True,
    },
    {
        "id": "cerebras",
        "name": "Cerebras",
        "kind": "openai_compat",
        "base_url": "https://api.cerebras.ai/v1",
        "keys_url": "https://cloud.cerebras.ai",
        "quota_api": None,
        "notes": "OpenAI-compatible.",
        # Cerebras Cloud offers an officially documented free API tier
        # (rate-limited) for its served models.
        "free_tier": True,
        "free_tier_source": "https://www.cerebras.ai/inference",
    },
    {
        "id": "together",
        "name": "Together AI",
        "kind": "openai_compat",
        "base_url": "https://api.together.xyz/v1",
        "keys_url": "https://api.together.xyz/settings/api-keys",
        "quota_api": None,
        "notes": "OpenAI-compatible.",
        "free_tier": False,   # paid per token; cannot verify per-model free
    },
    {
        "id": "custom",
        "name": "Custom OpenAI-compatible",
        "kind": "openai_compat",
        "base_url": "",
        "keys_url": "",
        "quota_api": None,
        "notes": "Any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp server...).",
        "free_tier": False,   # unknown endpoint — cannot verify
    },
]

BUILTIN_BY_ID = {p["id"]: p for p in BUILTIN_PROVIDERS}
