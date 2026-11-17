"""Model capability benchmarks — reproducible, real tests. No fake ratings.

Each capability runs a small, deterministic test against the actual model and
is scored by a mechanical validator (never by an LLM judge). Results are stored
in the benchmarks table with source='measured'. Capabilities without a test are
reported as 'untested'. Official spec data (context window) is stored with
source='official-spec'.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from ..ai.provider import ai_provider
from ..database.database import db
from ..database import repository as repo
from ..providers import manager as pm

CAPABILITIES = [
    "reasoning", "research_planning", "search_query_generation",
    "information_extraction", "structured_json", "source_verification",
    "summarization", "tool_calling", "coding", "long_context",
    "instruction_following",
]


def _save(model_ref: str, capability: str, score: float, source: str, detail: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO benchmarks (model_ref, capability, score, source, detail, tested_at)"
            " VALUES (?,?,?,?,?,?)",
            (model_ref, capability, score, source, detail[:500], repo.utcnow()))


def _run_async(coro):
    """Run an async AI call on its own event loop in a worker thread —
    reliable even when a loop is already running in the current context."""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


# model_ref that actually executed the most recent benchmark call (failovers
# mean it can differ from the requested model; scores must be attributed
# to the model that really ran)
_last_executed_ref: dict = {"ref": ""}


def _run(model_spec: str, messages: list[dict], request_type: str,
         max_tokens: int = 500, json_mode: bool = False) -> str:
    async def _go():
        return await ai_provider.chat_detailed(
            messages, request_type=f"bench_{request_type}", model_spec=model_spec,
            temperature=0.0, max_tokens=max_tokens, json_mode=json_mode)
    result = _run_async(_go())
    _last_executed_ref["ref"] = result.get("model_ref", "")
    return result["content"]


def _task_tool_call(model_spec: str) -> tuple[float, str]:
    """Tool calling: model must emit a well-formed web_search tool call."""
    tools = [{"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}}]

    async def _go():
        return await ai_provider.chat_with_tools(
            [{"role": "system", "content": "You must answer only with tool calls."},
             {"role": "user", "content": "Search for the 2024 Nobel physics prize."}],
            tools, model_spec=model_spec, request_type="bench_tool_calling")
    result = _run_async(_go())
    _last_executed_ref["ref"] = result.get("model_ref", "")
    calls = result.get("tool_calls") or []
    if calls and calls[0].get("name") == "web_search":
        try:
            args = json.loads(calls[0]["arguments"])
            if args.get("query"):
                return 10.0, "emitted valid web_search tool call"
        except Exception:
            return 5.0, "tool call present but arguments not valid JSON"
    return 0.0, "no valid tool call"


def _task_long_context(model_spec: str, model_row: dict) -> tuple[float, str]:
    """Needle-in-haystack with generated filler. The haystack is capped well
    below the model's context window (and under provider TPM limits) — the
    test measures retrieval, not the advertised maximum."""
    ctx = int(model_row.get("context_tokens") or 8000)
    ctx = min(ctx, 12000)                      # ~3k tokens of chars, TPM-safe
    needle = "The secret launch codename is ZEPHYR-7."
    filler = "The quarterly report covers standard operating metrics and logistics. "
    n = max(50, int(ctx * 3.5 / len(filler)))
    hay = filler * n
    pos = len(hay) // 3
    hay = hay[:pos] + " " + needle + " " + hay[pos:]
    if len(hay) > ctx * 3.5:
        hay = hay[: int(ctx * 3.5)]
    out = _run(model_spec, [
        {"role": "user", "content":
            f"Find the secret launch codename in this text. Reply with ONLY the codename.\n\n{hay}"}],
        "long_context", max_tokens=600)
    if "ZEPHYR-7" in out:
        return 10.0, f"needle found in ~{len(hay) // 4} token context"
    return round(10.0 * ("zephyr" in out.lower()), 1) or 0.0, "needle not found exactly"


TESTS = {
    "reasoning": lambda spec: _score(
        "Priya buys 4 apples at 2 rupees each and pays with a 20 rupee note. "
        "How many rupees of change does she get? Answer with the number only.",
        r"\b12\b", spec, "reasoning"),
    "structured_json": lambda spec: _score_json(
        "Return JSON {\"items\": [3 items], \"total\": sum} for cart: 2 apples ($1.50 each), "
        "1 bread ($2.00). Only JSON.", {"items": list, "total": (int, float)}, spec),
    "information_extraction": lambda spec: _score_json(
        'Extract from: "Acme Robotics was founded in 2015 by Priya Nair in Pune. '
        'Contact: team@acmerobotics.in". JSON with keys founder, year, city, email. Only JSON.',
        {"founder": str, "year": (str, int), "city": str, "email": str}, spec),
    "summarization": lambda spec: _bounded(
        spec, "Summarize in exactly one sentence of under 30 words: " + _long_text(),
        lambda out: 10.0 if 0 < len(out.split()) <= 30 else 4.0,
        "one sentence ≤30 words"),
    "search_query_generation": lambda spec: _score_json(
        "Generate 3 diverse web search queries to research AI startups in India. "
        'JSON {"queries": [...]}. Only JSON.', {"queries": list}, spec,
        extra_check=lambda d: 10.0 if 3 <= len(d.get("queries", [])) <= 4 else 6.0),
    "instruction_following": lambda spec: _bounded(
        spec, "List exactly 4 animals, one per line, no numbering, no extra text.",
        lambda out: 10.0 if len([l for l in out.strip().splitlines() if l.strip()]) == 4
        else 0.0, "exactly 4 lines"),
    "coding": lambda spec: _coding(spec),
    "research_planning": lambda spec: _score_json(
        'Plan research on "impact of AI on Indian agriculture". JSON {"objective": str, '
        '"sub_questions": [3-5]}. Only JSON.', {"objective": str, "sub_questions": list}, spec),
    "source_verification": lambda spec: _score(
        'Claim: "The Eiffel Tower was completed in 1939." Given source text: "The Eiffel '
        'Tower, finished in 1889, is in Paris." Should the claim be accepted from this '
        'source? Answer VERIFIED, CONTRADICTED or UNVERIFIED only.',
        r"\bCONTRADICTED\b", spec, "verification"),
}


def _long_text() -> str:
    return ("Neural networks process data through layered transformations. Training adjusts "
            "weights via gradient descent. Backpropagation computes gradients efficiently. "
            "Attention mechanisms weigh token relevance dynamically. Transformers replaced "
            "recurrence with parallel attention. ")


def _score(prompt: str, pattern: str, spec: str, kind: str) -> tuple[float, str]:
    out = _run(spec, [{"role": "user", "content": prompt}], kind)
    ok = re.search(pattern, out) is not None
    return (10.0 if ok else 0.0), f"expected /{pattern}/ → {'match' if ok else 'no match'}"


def _score_json(prompt: str, shape: dict, spec: str,
                extra_check=None) -> tuple[float, str]:
    from ..providers.adapter import parse_json_loose, AdapterError
    out = _run(spec, [{"role": "user", "content": prompt}], "json", json_mode=True)
    try:
        data = parse_json_loose(out)
        assert isinstance(data, dict), "not an object"
        for k, t in shape.items():
            v = data.get(k)
            assert isinstance(v, t), f"{k} wrong type {type(v)}"
        score = 10.0
        detail = "valid JSON + correct types"
        if extra_check:
            score = extra_check(data)
            detail += f" (structural check → {score})"
        return score, detail
    except (AdapterError, AssertionError) as e:
        return 0.0, f"invalid: {str(e)[:120]}"


def _bounded(spec: str, prompt: str, check, desc: str) -> tuple[float, str]:
    out = _run(spec, [{"role": "user", "content": prompt}], "instruct")
    return check(out), desc


def _coding(spec: str) -> tuple[float, str]:
    out = _run(spec, [
        {"role": "user", "content":
            "Write a Python function `def is_palindrome(s: str) -> bool:` that ignores case "
            "and non-alphanumerics. Return ONLY the code, no fences."}], "coding")
    try:
        code = out.strip().removeprefix("```python").removesuffix("```").strip()
        ns: dict = {}
        exec(code, ns)  # validated in a throwaway namespace, never executed on files
        fn = ns.get("is_palindrome")
        tests = [("A man, a plan, a canal: Panama", True), ("hello", False),
                 ("", True), ("No 'x' in Nixon", True)]
        passed = sum(1 for s, want in tests if fn(s) == want)
        return (10.0 * passed / len(tests)), f"{passed}/{len(tests)} test cases passed"
    except Exception as e:
        return 0.0, f"code failed to run: {str(e)[:120]}"


async def run_benchmark(model_spec: str, capabilities: list[str] | None = None) -> dict:
    model_row = pm.resolve_model(model_spec) if model_spec and model_spec != "auto" else \
        pm.resolve_model("", "")
    if not model_row:
        return {"error": f"model {model_spec!r} not configured"}
    ref = model_row["id"]
    caps = capabilities or CAPABILITIES
    results = {}
    for cap in caps:
        test = TESTS.get(cap)
        try:
            if cap == "tool_calling":
                score, detail = _task_tool_call(model_spec)
            elif cap == "long_context":
                score, detail = _task_long_context(model_spec, model_row)
            elif test:
                score, detail = test(model_spec)
            else:
                _save(ref, cap, 0, "untested", "no benchmark defined")
                results[cap] = {"score": None, "status": "untested"}
                continue
        except Exception as e:
            score, detail = 0.0, f"benchmark error: {str(e)[:200]}"
        # Attribution honesty: if a failover happened (e.g. requested model
        # rate-limited and another model executed), save the score under the
        # model that actually ran — never misattribute results.
        executed = _last_executed_ref.get("ref", "") or ref
        if executed != ref:
            detail = f"{detail} [executed on {executed} — requested model failed over]"
        _save(executed, cap, score, "measured", detail)
        results[cap] = {"score": score, "detail": detail, "status": "measured",
                        "executed_on": executed}
    # official spec: context window from catalog
    _save(ref, "context_window_tokens", float(model_row.get("context_tokens") or 0),
          "official-spec", "provider catalog value")
    return {"model_ref": ref, "results": results}


def get_benchmarks(model_ref: Optional[str] = None) -> list[dict]:
    with db() as conn:
        if model_ref:
            rows = conn.execute(
                "SELECT * FROM benchmarks WHERE model_ref=? ORDER BY tested_at DESC",
                (model_ref,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM benchmarks ORDER BY tested_at DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


def summarize(model_ref: str) -> dict:
    """Latest score per capability; measured scores only in the average."""
    latest: dict[str, dict] = {}
    for b in get_benchmarks(model_ref):
        cap = b["capability"]
        if cap not in latest:
            latest[cap] = b
    measured = [b["score"] for b in latest.values()
                if b["source"] == "measured" and b["capability"] != "context_window_tokens"]
    return {
        "model_ref": model_ref,
        "capabilities": latest,
        "average_measured": round(sum(measured) / len(measured), 2) if measured else None,
        "note": "average covers measured capabilities only; 'official-spec' rows are "
                "not performance scores",
    }
