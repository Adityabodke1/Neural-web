"""Model adapters: one interface, any provider.

- openai_compat: every OpenAI-compatible endpoint (Groq, OpenAI, OpenRouter,
  Cerebras, Together, custom) via the official `openai` SDK with custom base_url.
- gemini: native Gemini REST API via httpx.

Both return the same dict shape:
  {'content', 'tool_calls', 'usage', 'model_ref', 'latency_ms'}
and raise AdapterError subclasses on failure. API keys are never logged.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from ..database.models import ExtractedRecord  # noqa: F401  (type compat)
from .registry import BUILTIN_BY_ID

log = logging.getLogger("neuralweb.adapters")


class AdapterError(Exception):
    pass


class AdapterRateLimitError(AdapterError):
    pass


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    return t


def parse_json_loose(content: str) -> Any:
    t = _strip_fence(content)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError:
                        break
    raise AdapterError("model did not return valid JSON")


class OpenAICompatAdapter:
    """Works with Groq, OpenAI, OpenRouter, Cerebras, Together and custom endpoints."""

    def __init__(self, provider: dict, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key
        self._client: Any = None

    def _ensure(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            base = self.provider["base_url"]
            if not base:
                raise AdapterError(f"provider '{self.provider['id']}' has no base_url configured")
            self._client = OpenAI(api_key=self.api_key, base_url=base, timeout=90, max_retries=0)
        return self._client

    def chat(self, model_id: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int | None = None, json_mode: bool = False,
             tools: list[dict] | None = None) -> dict:
        client = self._ensure()
        kwargs: dict[str, Any] = {
            "model": model_id, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens or 4096,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        started = time.monotonic()
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                raise AdapterRateLimitError(msg[:300]) from e
            raise AdapterError(msg[:300]) from e
        latency = int((time.monotonic() - started) * 1000)
        usage = getattr(resp, "usage", None)
        msg_obj = resp.choices[0].message if resp.choices else None
        tool_calls = []
        for tc in (getattr(msg_obj, "tool_calls", None) or []):
            fn = getattr(tc, "function", None)
            tool_calls.append({"id": getattr(tc, "id", ""), "name": getattr(fn, "name", ""),
                               "arguments": getattr(fn, "arguments", "{}")})
        return {
            "content": (msg_obj.content if msg_obj else "") or "",
            "message": msg_obj,
            "tool_calls": tool_calls,
            "usage": {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            },
            "model_ref": f"{self.provider['id']}/{model_id}",
            "latency_ms": latency,
        }

    def list_models(self) -> list[dict]:
        client = self._ensure()
        try:
            models = client.models.list()
        except Exception as e:
            raise AdapterError(f"listing models failed: {e}") from e
        out = []
        for m in models.data[:300]:
            mid = getattr(m, "id", "")
            out.append({"model_id": mid,
                        "label": mid,
                        "context_tokens": getattr(m, "context_window", None) or
                                          getattr(m, "max_context_window", None)})
        return out

    def provider_reported_quota(self) -> dict | None:
        """OpenRouter exposes remaining credits on /auth/key."""
        if self.provider["id"] != "openrouter":
            return None
        try:
            resp = httpx.get("https://openrouter.ai/api/v1/auth/key",
                             headers={"Authorization": f"Bearer {self.api_key}"}, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            limit = data.get("limit")
            usage = data.get("usage")
            if limit is not None:
                return {"limit": float(limit), "used": float(usage or 0),
                        "unit": "credits", "source": "provider-reported"}
        except Exception as e:
            log.debug("openrouter quota fetch failed: %s", e)
        return None


class GeminiAdapter:
    """Native Gemini REST API (generativelanguage.googleapis.com)."""

    def __init__(self, provider: dict, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base = provider["base_url"].rstrip("/")

    def chat(self, model_id: str, messages: list[dict], temperature: float = 0.2,
             max_tokens: int | None = None, json_mode: bool = False,
             tools: list[dict] | None = None) -> dict:
        contents, system_parts = [], []
        tool_map: dict[str, str] = {}
        declarations = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                system_parts.append(m["content"])
                continue
            if role == "assistant" and m.get("tool_calls"):
                parts = []
                for tc in m["tool_calls"]:
                    try:
                        args = json.loads(tc["arguments"])
                    except Exception:
                        args = {}
                    parts.append({"functionCall": {"name": tc["name"], "args": args}})
                contents.append({"role": "model", "parts": parts})
                continue
            if role == "tool":
                try:
                    result = json.loads(m["content"])
                except Exception:
                    result = {"result": m["content"]}
                contents.append({"role": "user", "parts": [
                    {"functionResponse": {"name": tool_map.get(m.get("tool_call_id", ""), "tool"),
                                          "response": result}}]})
                continue
            text = m.get("content") or ""
            contents.append({"role": "user" if role != "assistant" else "model",
                             "parts": [{"text": text}]})
        if tools:
            for t in tools:
                fn = t.get("function", {})
                name = fn.get("name", "")
                tool_map[fn.get("_call_id", name)] = name
                declarations.append({
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": _clean_gemini_schema(fn.get("parameters")),
                })
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature,
                                 "maxOutputTokens": max_tokens or 4096},
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
        if declarations:
            payload["tools"] = [{"functionDeclarations": declarations}]
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{self.base}/models/{model_id}:generateContent?key={self.api_key}"
        started = time.monotonic()
        try:
            resp = httpx.post(url, json=payload, timeout=90)
            if resp.status_code == 429:
                raise AdapterRateLimitError(resp.text[:300])
            resp.raise_for_status()
            data = resp.json()
        except AdapterRateLimitError:
            raise
        except Exception as e:
            raise AdapterError(str(e)[:300]) from e
        latency = int((time.monotonic() - started) * 1000)
        cand = (data.get("candidates") or [{}])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts)
        tool_calls = []
        for i, p in enumerate(parts):
            fc = p.get("functionCall")
            if fc:
                tool_calls.append({"id": f"gemini_{i}", "name": fc.get("name", ""),
                                   "arguments": json.dumps(fc.get("args", {}))})
        um = data.get("usageMetadata", {})
        return {
            "content": content,
            "message": None,
            "tool_calls": tool_calls,
            "usage": {"prompt_tokens": um.get("promptTokenCount", 0) or 0,
                      "completion_tokens": um.get("candidatesTokenCount", 0) or 0},
            "model_ref": f"gemini/{model_id}",
            "latency_ms": latency,
        }

    def list_models(self) -> list[dict]:
        try:
            resp = httpx.get(
                f"{self.base}/models?key={self.api_key}", timeout=20)
            resp.raise_for_status()
        except Exception as e:
            raise AdapterError(f"listing models failed: {e}") from e
        out = []
        for m in resp.json().get("models", []):
            mid = (m.get("name") or "").removeprefix("models/")
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append({"model_id": mid, "label": m.get("displayName", mid),
                            "context_tokens": m.get("inputTokenLimit")})
        return out

    def provider_reported_quota(self) -> dict | None:
        return None


def _clean_gemini_schema(schema: Any, depth: int = 0) -> Any:
    """Convert JSON schema to Gemini-compatible subset (drop unsupported keys)."""
    if not isinstance(schema, dict) or depth > 6:
        return None
    allowed = {"type", "description", "properties", "required", "items", "enum",
               "format", "minimum", "maximum"}
    out: dict = {}
    t = schema.get("type")
    if t == "object":
        out["type"] = "OBJECT"
    elif t == "string":
        out["type"] = "STRING"
    elif t == "number" or t == "integer":
        out["type"] = "NUMBER" if t == "number" else "INTEGER"
    elif t == "boolean":
        out["type"] = "BOOLEAN"
    elif t == "array":
        out["type"] = "ARRAY"
        item = _clean_gemini_schema(schema.get("items"), depth + 1)
        if item:
            out["items"] = item
    if t in (None, "object"):
        props = schema.get("properties")
        if isinstance(props, dict):
            out["properties"] = {k: _clean_gemini_schema(v, depth + 1) or {"type": "STRING"}
                                 for k, v in props.items()}
    if schema.get("required"):
        out["required"] = schema["required"]
    if schema.get("description"):
        out["description"] = schema["description"]
    return out or None


def build_adapter(provider: dict, api_key: str):
    kind = provider.get("kind", "openai_compat")
    if kind == "gemini":
        return GeminiAdapter(provider, api_key)
    return OpenAICompatAdapter(provider, api_key)
