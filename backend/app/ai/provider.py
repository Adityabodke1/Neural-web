"""Central AI routing facade. ALL AI components go through `ai_provider`.

- resolves the model per task (user-selected --model, task assignment, or default)
- falls back to another enabled model (preferring a different provider) on failure
- exponential backoff on rate limits / transient errors
- token usage + latency logged with provider & model (never logs keys)
- strict JSON parsing + schema validation with one repair retry
- raises AIError when every candidate fails — never fabricates success
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import jsonschema

from ..config import settings
from ..database import repository as repo
from ..providers import manager as pm
from ..providers.adapter import AdapterError, AdapterRateLimitError, parse_json_loose

log = logging.getLogger("neuralweb.ai")

_BACKOFF_BASE = 2.0
_BACKOFF_MAX = 20.0


class AIError(Exception):
    pass


def _candidate_models(model_spec: str, task: str) -> list[dict]:
    """Ordered list of model configs to try: requested → task default → global default →
    other enabled models (prefer different provider for resilience)."""
    candidates: list[dict] = []
    try:
        m = pm.resolve_model(model_spec, task=task)
        if m:
            candidates.append(m)
    except ValueError as e:
        if model_spec and model_spec != "auto":
            raise AIError(str(e))
    seen = {m["id"] for m in candidates}
    for extra in pm.list_models():
        if extra["id"] not in seen and extra.get("enabled"):
            candidates.append(extra)
            seen.add(extra["id"])
    return candidates


class Provider:
    """Facade used by planner / extractor / analyzer / verifier / reporter / agents."""

    def __init__(self) -> None:
        self._rate_limited_until: dict[str, float] = {}  # model_ref → monotonic

    async def chat(self, messages: list[dict], *, request_type: str = "chat",
                   session_id: Optional[str] = None, task: str = "",
                   model_spec: str = "", temperature: float = 0.2,
                   max_tokens: Optional[int] = None, json_mode: bool = False) -> str:
        result = await self._complete(
            messages, request_type=request_type, session_id=session_id, task=task,
            model_spec=model_spec, temperature=temperature, max_tokens=max_tokens,
            json_mode=json_mode)
        return result["content"]

    async def chat_detailed(self, messages: list[dict], *, request_type: str = "chat",
                            session_id: Optional[str] = None, task: str = "",
                            model_spec: str = "", temperature: float = 0.2,
                            max_tokens: Optional[int] = None,
                            json_mode: bool = False) -> dict:
        """Full result incl. model_ref/provider — lets callers verify exactly
        which model executed (e.g. benchmark attribution under failover)."""
        return await self._complete(
            messages, request_type=request_type, session_id=session_id, task=task,
            model_spec=model_spec, temperature=temperature, max_tokens=max_tokens,
            json_mode=json_mode)

    async def chat_json(self, messages: list[dict], *, request_type: str,
                        session_id: Optional[str] = None, task: str = "",
                        model_spec: str = "", schema: Optional[dict] = None,
                        temperature: float = 0.1,
                        max_tokens: Optional[int] = None) -> Any:
        content = await self.chat(
            messages, request_type=request_type, session_id=session_id, task=task,
            model_spec=model_spec, temperature=temperature, max_tokens=max_tokens,
            json_mode=True)
        try:
            data = parse_json_loose(content)
            if schema is not None:
                jsonschema.validate(data, schema)
            return data
        except (AdapterError, jsonschema.ValidationError, ValueError) as e:
            log.warning("JSON parse/validate failed (%s); requesting repair", str(e)[:200])
            repair = await self.chat(
                messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content":
                        "The previous answer was not valid JSON or failed schema validation.\n"
                        f"Error: {e}\nReturn ONLY the corrected JSON object, no prose, no fences."}
                ],
                request_type=request_type + "_repair", session_id=session_id, task=task,
                model_spec="", temperature=0.0, max_tokens=max_tokens, json_mode=True)
            data = parse_json_loose(repair)
            if schema is not None:
                jsonschema.validate(data, schema)
            return data

    async def chat_with_tools(self, messages: list[dict], tools: list[dict], *,
                              request_type: str = "agent_step",
                              session_id: Optional[str] = None, task: str = "",
                              model_spec: str = "", temperature: float = 0.2,
                              max_tokens: Optional[int] = None) -> dict:
        result = await self._complete(
            messages, request_type=request_type, session_id=session_id, task=task,
            model_spec=model_spec, temperature=temperature, max_tokens=max_tokens,
            tools=tools)
        return result

    # ------------------------------------------------------------ core

    @staticmethod
    def _retry_after(err_text: str) -> float:
        """Parse a provider's explicit 'try again in 13.155s / 802.5ms' hint.
        Returns seconds (capped) or 0 when no hint is present."""
        import re
        m = re.search(r"try again in (\d+(?:\.\d+)?)\s*(ms|s|seconds?)", err_text.lower())
        if not m:
            return 0.0
        val = float(m.group(1))
        if m.group(2) == "ms":
            val /= 1000.0
        return min(val + 1.0, 60.0)

    async def _complete(self, messages: list[dict], *, request_type: str,
                        session_id: Optional[str], task: str, model_spec: str,
                        temperature: float, max_tokens: Optional[int],
                        json_mode: bool = False, tools: Optional[list[dict]] = None) -> dict:
        candidates = _candidate_models(model_spec, task)
        if not candidates:
            raise AIError("No AI models configured. Add one with `neural-web models add` "
                          "or the dashboard Providers page.")
        now = asyncio.get_event_loop().time()
        # Skip models that just rate-limited (unless they are the only option):
        # free tiers are per-model limited, so quick failover beats long backoff.
        preferred = [m for m in candidates
                     if self._rate_limited_until.get(m["id"], 0) <= now]
        order = preferred + [m for m in candidates if m not in preferred]
        last_error: Any = None
        rate_limited_ids: set[str] = set()
        for m in order:
            try:
                adapter = pm._adapter_for(m["provider_id"])
            except AdapterError as e:
                last_error = e
                log.warning("provider %s unavailable: %s", m["provider_id"], e)
                continue
            attempt = 0
            while attempt <= settings.AI_MAX_RETRIES:
                try:
                    result = await asyncio.to_thread(
                        adapter.chat, m["model_id"], messages, temperature,
                        max_tokens, json_mode, tools)
                    usage = result["usage"]
                    repo.log_usage(
                        session_id=session_id, model=m["model_id"], request_type=request_type,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        latency_ms=result["latency_ms"], ok=True,
                        provider=m["provider_id"])
                    result["model_ref"] = m["id"]
                    result["provider"] = m["provider_id"]
                    return result
                except AdapterRateLimitError as e:
                    last_error = e
                    rate_limited_ids.add(m["id"])
                    # Honor the provider's own 'try again in X' hint when it is
                    # short; otherwise use a short cooldown and fail over —
                    # long exponential backoff burns the agent's budget.
                    wait = self._retry_after(str(e)) or 25.0
                    self._rate_limited_until[m["id"]] = \
                        asyncio.get_event_loop().time() + wait
                    delay = 2.0 if attempt == 0 else 4.0
                    log.warning("rate limited on %s (attempt %d), cooldown %.1fs, backoff %.1fs then failover",
                                m["id"], attempt + 1, wait, delay)
                    repo.log_usage(session_id=session_id, model=m["model_id"],
                                   request_type=request_type, prompt_tokens=0,
                                   completion_tokens=0, latency_ms=0, ok=False,
                                   error=f"rate limited: {e}", provider=m["provider_id"])
                    await asyncio.sleep(delay)
                    attempt += 1
                    if attempt > 1:
                        break  # fail over to the next candidate model
                except AdapterError as e:
                    last_error = e
                    err = str(e)
                    transient = any(x in err.lower() for x in ("429", "rate", "503", "timeout"))
                    if "429" in err or "rate" in err.lower():
                        # some adapters wrap rate limits in a generic error
                        rate_limited_ids.add(m["id"])
                        self._rate_limited_until[m["id"]] = \
                            asyncio.get_event_loop().time() + \
                            (self._retry_after(err) or 25.0)
                    if tools and ("tool calling" in err.lower()
                                  or "tool_use" in err.lower()
                                  or "function calling" in err.lower()):
                        # this model can't do tool calling at all — stop
                        # offering it for tool requests for a long while
                        self._rate_limited_until[m["id"]] = \
                            asyncio.get_event_loop().time() + 3600.0
                        log.warning("model %s does not support tool calling; "
                                    "excluded from tool requests for 1h", m["id"])
                    repo.log_usage(session_id=session_id, model=m["model_id"],
                                   request_type=request_type, prompt_tokens=0,
                                   completion_tokens=0, latency_ms=0, ok=False,
                                   error=err[:500], provider=m["provider_id"])
                    log.warning("AI error on %s: %s", m["id"], err[:300])
                    if transient and attempt < settings.AI_MAX_RETRIES:
                        await asyncio.sleep(min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** attempt)))
                        attempt += 1
                        continue
                    break  # next candidate model
        # Every candidate hit a rate limit? Wait out the SHORTEST cooldown and
        # try one final pass — free-tier tasks otherwise die on a 10-second
        # window even though waiting would let them finish.
        if rate_limited_ids and len(rate_limited_ids) >= len(candidates):
            now = asyncio.get_event_loop().time()
            earliest = min(self._rate_limited_until.get(m["id"], 0) for m in candidates)
            wait_s = earliest - now
            if 0 < wait_s <= 45.0:
                log.warning("all models rate limited; waiting %.1fs for the shortest cooldown",
                            wait_s)
                await asyncio.sleep(wait_s + 0.5)
                return await self._complete(
                    messages, request_type=request_type, session_id=session_id,
                    task=task, model_spec=model_spec, temperature=temperature,
                    max_tokens=max_tokens, json_mode=json_mode, tools=tools)
        raise AIError(f"All configured models failed. Last error: {last_error}")

    # ------------------------------------------------------------ status

    def status(self) -> dict:
        models = pm.list_models()
        active = None
        try:
            active = pm.resolve_model("auto")
        except Exception:
            pass
        return {
            "active_model": active["id"] if active else None,
            "models_configured": len(models),
            "models": [{"id": m["id"], "provider": m["provider_id"],
                        "is_default": bool(m["is_default"])} for m in models],
        }


def text_block(text: str, limit: int = 12000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: int(limit * 0.7)] + "\n...[content truncated]...\n" + text[-int(limit * 0.2):]


def records_block(records: list[dict], limit_records: int = 60) -> str:
    slim = []
    for r in records[:limit_records]:
        slim.append({"data": r.get("data"), "source_url": r.get("source_url") or r.get("url")})
    return json.dumps(slim, ensure_ascii=False)


ai_provider = Provider()
