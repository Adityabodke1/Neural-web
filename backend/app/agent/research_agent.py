"""The autonomous research agent.

An agentic loop where Groq decides — via tool calls — which searches to run,
which pages to scrape, what to extract, when information is missing and when
to stop. Hard budget guards (steps, pages, time, stop flag) prevent infinite
loops. Every extraction is stored with field-level source provenance.
"""
from __future__ import annotations

import asyncio
import json
import re
import logging
import time
from typing import Any

from ..ai.extractor import extractor
from ..ai.planner import DEPTH_CONFIG, planner
from ..ai.provider import ai_provider, text_block
from ..ai.analyzer import analyzer
from ..database import repository as repo
from ..database.models import ExtractedRecord
from ..memory.embeddings import embedder
from ..memory.vector_store import vector_store
from ..scraping.engine import scrape_engine
from .task_manager import TaskManager
from .verification import verify_record

log = logging.getLogger("neuralweb.agent")

TOOL_RESULT_LIMIT = 3500  # chars of tool result fed back into context

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web with a natural-language query. Returns ranked "
                           "results with URL, title and snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_page",
            "description": "Scrape a URL and return its clean main content, title and "
                           "metadata. Use after web_search to read promising results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "use_js": {"type": "boolean",
                               "description": "Set true for JavaScript-heavy pages"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_fields",
            "description": "Extract the target fields from a URL into a structured record "
                           "with per-field source URLs and evidence. The record is saved "
                           "automatically with provenance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["url", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_finding",
            "description": "Save a general research finding (when no tabular fields are "
                           "requested). The finding must be backed by the given URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "topic": {"type": "string"},
                    "finding": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["url", "finding"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_page",
            "description": "Summarize what a page contributes to the research objective "
                           "(useful to judge relevance before deeper extraction).",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_record",
            "description": "Cross-check one saved record against an independent source. "
                           "Returns updated verification status for its fields.",
            "parameters": {
                "type": "object",
                "properties": {"record_id": {"type": "integer"}},
                "required": ["record_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_research",
            "description": "Call when the research request is satisfied OR the budget is "
                           "nearly exhausted. Triggers the final report generation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": [],
            },
        },
    },
]

SYSTEM_TEMPLATE = """You are NEURAL WEB, an autonomous web research agent.

RESEARCH REQUEST:
{prompt}

PLAN (objective): {objective}
Sub-questions: {sub_questions}
Target fields: {target_fields}

TOOLS & STRATEGY:
- Start with web_search on the angles from the plan. Prefer 3-6 diverse queries.
- Read promising results with scrape_page; judge relevance via summarize_page.
- {extraction_instruction}
- Use save_finding for general findings (each must be backed by a URL you scraped).
- Cross-check important facts with verify_record (records 1..{max_records} are most important).
- You may run additional searches when information is missing.
- Call finish_research when the request is answered, target fields are filled, or
  you are running out of budget. Do NOT repeat the same search twice.

BUDGET: max {max_pages} pages, about {max_steps} tool steps. Elapsed time limit enforced externally.

CHAT WITH THE USER: alongside your tool calls, write one or two short, friendly
sentences narrating what you're doing and what you found (like Claude Code).
The user sees your text live — keep it natural and brief.

RULES:
- Only use information actually returned by tools. Never fabricate facts or URLs.
- Track what is missing and adapt queries. Avoid duplicate work.
- Extraction calls must list the exact target fields."""


_SNIPPET_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "in", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "there", "this", "to", "was", "were", "what", "which", "who", "will",
    "with", "name", "one", "also", "list", "directed", "sep", "nov", "mar",
    "jan", "feb", "apr", "jun", "jul", "aug", "oct", "dec",
}


def _content_words(text: str) -> set[str]:
    """Lowercased alphabetic words minus stopwords — used for deterministic
    snippet relevance filtering."""
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())
            if w not in _SNIPPET_STOPWORDS}


class ResearchAgent:
    def __init__(self, session_id: str, tm: TaskManager) -> None:
        self.session_id = session_id
        self.tm = tm
        self.session = repo.get_session(session_id) or {}
        self.params: dict = json.loads(self.session.get("params_json", "{}"))
        self.prompt = self.session.get("prompt", "")
        self.depth = self.session.get("depth", "standard")
        self.deadline: float = 0.0
        self.page_summaries: list[dict] = []
        self.record_ids: list[int] = []
        self.steps_used = 0

    # ------------------------------------------------------------------ run

    async def run(self, resume: bool = False) -> None:
        try:
            self._start(resume)
            plan = await self._plan()
            await self._agent_loop(plan, resume)
            # Last-resort resilience: if every page scrape was blocked (robots /
            # anti-bot), extract from the stored search snippets so the session
            # still yields clearly-labelled, snippet-only provenance records.
            if self._record_count() == 0:
                await self._snippet_extraction_pass()
            await self._report(plan)
            self._finish("complete")
        except asyncio.CancelledError:
            self._finish("stopped", error="cancelled")
            raise
        except Exception as e:
            log.exception("agent failed for session %s", self.session_id)
            self.tm.event("error", {"message": str(e)[:400]})
            self._finish("failed", error=str(e)[:500])
        finally:
            await self._persist_counts()

    def _start(self, resume: bool) -> None:
        depth_cfg = DEPTH_CONFIG.get(self.depth, DEPTH_CONFIG["standard"])
        params = self.params
        self.max_pages = int(params.get("max_pages", 12 if self.depth != "quick" else 5))
        self.max_steps = int(params.get("max_steps", 0)) or 14 * depth_cfg["max_rounds"]
        self.max_records = int(params.get("max_records", 0)) or self.max_pages
        minutes = float(params.get("time_limit_minutes", 0)) or \
            {"quick": 4, "standard": 8, "deep": 15}.get(self.depth, 8)
        self.deadline = time.monotonic() + minutes * 60
        self.target_fields: list[str] = params.get("target_fields", [])
        self.include_domains: list[str] = params.get("include_domains", [])
        self.exclude_domains: list[str] = params.get("exclude_domains", [])
        self.seen_urls: set[str] = set()
        repo.update_session(self.session_id, status="running")
        self.tm.set_step("planning", "starting research engine")
        if resume:
            for r in repo.list_records(self.session_id):
                self.record_ids.append(r["id"])

    async def _plan(self) -> dict:
        plan = await planner.build_plan(self.prompt, self.session_id)
        if not self.target_fields and plan.get("target_fields"):
            self.target_fields = plan["target_fields"][:15]
        repo.update_session(self.session_id, plan_json=json.dumps(plan, ensure_ascii=False))
        self.tm.event("plan", {"plan": plan})
        self.tm.set_step("searching", "generating search queries")
        return plan

    # ----------------------------------------------------------- agent loop

    async def _agent_loop(self, plan: dict, resume: bool) -> None:
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_TEMPLATE.format(
                prompt=self.prompt,
                objective=plan.get("objective", ""),
                sub_questions=plan.get("sub_questions", []),
                target_fields=self.target_fields or "(general research)",
                extraction_instruction=(
                    "For every relevant page call extract_fields with the target fields — "
                    "this saves a provenance-backed record automatically."
                    if self.target_fields else
                    "No specific fields requested: gather findings via save_finding and "
                    "summarize pages with summarize_page."),
                max_pages=self.max_pages,
                max_steps=self.max_steps,
                max_records=min(self.max_records, len(self.record_ids) + 1),
            )},
        ]
        if resume:
            prior = repo.list_records(self.session_id)
            messages[0]["content"] += (
                f"\n\nRESUMING: this session already has {len(prior)} records. Do not redo "
                "completed work; focus on gaps:\n" + json.dumps(
                    [r["data"].get("entity_name") for r in prior[:40]], ensure_ascii=False))
        seed = await planner.generate_queries(self.prompt, plan, 3, self.session_id)
        messages.append({"role": "user", "content":
            "Begin the research. Suggested initial queries: " + json.dumps(seed)})

        finish_reason = ""
        while self.steps_used < self.max_steps:
            if self.tm.stop_requested:
                finish_reason = "stopped by user"
                break
            if time.monotonic() > self.deadline:
                finish_reason = "time budget exhausted"
                break
            self.steps_used += 1
            self.tm.stats["ai_calls"] += 1
            try:
                result = await ai_provider.chat_with_tools(
                    messages, TOOLS, request_type="agent_step",
                    session_id=self.session_id)
            except Exception as e:
                log.warning("agent LLM step failed: %s", e)
                self.tm.event("warning", {"message": f"AI step failed: {e}"})
                break
            usage = result["usage"]
            self.tm.stats["ai_tokens"] += usage["prompt_tokens"] + usage["completion_tokens"]
            content = result.get("content") or ""
            tool_calls = result.get("tool_calls") or []
            if not tool_calls:
                # Model answered in prose: show it, then nudge it to act or finish
                text = content[:1500]
                if content.strip():
                    self.tm.event("note", {"text": content.strip()[:500]})
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content":
                    "Continue with tool calls (a short sentence for the user is fine "
                    "alongside them). If the research is done, call finish_research now."})
                if self.steps_used >= self.max_steps - 2:
                    break
                continue
            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls
                ],
            })
            # surface the model's own narration so it chats while researching
            narration = content.strip()
            if narration:
                self.tm.event("note", {"text": narration[:500]})
            # Sanitize hallucinated tool names: keep valid ones, reply with an
            # error tool message for invalid ones so the next request validates.
            valid_names = {t["function"]["name"] for t in TOOLS}
            for tc in tool_calls:
                if tc["name"] not in valid_names:
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": json.dumps({
                            "error": f"unknown tool '{tc['name']}'. Available tools: "
                                     + ", ".join(sorted(valid_names))})})
            for tc in tool_calls:
                if tc["name"] not in valid_names:
                    continue  # error tool message already added above
                if self.tm.stop_requested or time.monotonic() > self.deadline:
                    finish_reason = finish_reason or "budget exhausted"
                    break
                out = await self._execute_tool(tc)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": out[:TOOL_RESULT_LIMIT]})
                if tc["name"] == "finish_research":
                    try:
                        finish_reason = json.loads(tc["arguments"]).get("reason", "")
                    except Exception:
                        finish_reason = "agent finished"
            if finish_reason:
                break
        self.tm.event("loop_done", {"reason": finish_reason or "step budget exhausted",
                                    "steps": self.steps_used})

    # -------------------------------------------------------- tool execution

    async def _snippet_extraction_pass(self) -> None:
        """Deterministic fallback when no page could be scraped.

        Records are built directly from the stored search snippets — no LLM
        is involved, so the record text IS the evidence. Provenance is
        deliberately weaker: verification='unverified', confidence capped.
        Only snippets lexically relevant to the request are kept (≥2 shared
        content words) so irrelevant SERP hits don't become noise records."""
        prompt_words = _content_words(self.prompt)
        urls = repo.list_urls(self.session_id)
        snippets = []
        for u in sorted(urls, key=lambda x: -(x["score"] or 0)):
            s = (u["snippet"] or "").strip()
            if not s:
                continue
            if len(prompt_words & _content_words(f"{u['title']} {s}")) >= 2:
                snippets.append((u["url"], u["title"], s, u["score"]))
        if not snippets:
            return
        self.tm.event("warning", {
            "message": f"no page could be scraped; storing {min(len(snippets), 5)} "
                       f"relevant search snippets as snippet-only records "
                       f"(unverified)"})
        self.tm.set_step("extracting", "storing snippet-only records (degraded mode)")
        for url, title, snippet, _score in snippets[:5]:
            if self._record_count() >= self.max_records:
                break
            if not self.target_fields:
                # single honest record per snippet, exactly what the snippet says
                rec = ExtractedRecord(
                    data={"entity_name": title[:100] or url[:80],
                          "fields": {"snippet_finding": {
                              "value": snippet[:400],
                              "source_url": url,
                              "evidence": snippet[:300],
                              "confidence": 0.4,
                              "verification": "unverified",
                              "cross_check_source": None}},
                          "sources": [url],
                          "notes": "snippet-only record: the page could not be "
                                   "scraped (robots/anti-bot); value is verbatim "
                                   "from the search result snippet"},
                    url=url, confidence=0.4, verification="unverified")
            else:
                # target fields requested: label every requested field missing
                # and keep the snippet as evidence — do NOT guess values
                rec = ExtractedRecord(
                    data={"entity_name": title[:100] or url[:80],
                          "fields": {k: {"value": None, "source_url": url,
                                         "evidence": snippet[:300],
                                         "confidence": 0.0,
                                         "verification": "missing"}
                                     for k in self.target_fields},
                          "snippet_evidence": snippet[:400],
                          "sources": [url],
                          "notes": "page unscrapeable (robots/anti-bot); "
                                   "requested fields not verifiable from the "
                                   "snippet alone — run with Tavily/Firecrawl "
                                   "or deeper budget for full extraction"},
                    url=url, confidence=0.4, verification="unverified")
            rid = self._save_record(rec, None)
            if rid:
                self.tm.event("record", {"record_id": rid, "url": url,
                                         "entity": rec.data.get("entity_name"),
                                         "source_quality": "snippet-only"})

    async def _execute_tool(self, tc: dict) -> str:
        name, args = tc["name"], tc["arguments"]
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except Exception:
            args = {}
        self.tm.current_action = name
        try:
            if name == "web_search":
                return await self._tool_search(args)
            if name == "scrape_page":
                return await self._tool_scrape(args)
            if name == "extract_fields":
                return await self._tool_extract(args)
            if name == "save_finding":
                return await self._tool_finding(args)
            if name == "summarize_page":
                return await self._tool_summarize(args)
            if name == "verify_record":
                return await self._tool_verify(args)
            if name == "finish_research":
                return json.dumps({"ok": True, "note": "finalizing research"})
            return json.dumps({"error": f"unknown tool {name}"})
        except Exception as e:
            log.exception("tool %s failed", name)
            self.tm.event("error", {"tool": name, "message": str(e)[:300]})
            return json.dumps({"error": f"{name} failed: {e}"})

    async def _tool_search(self, args: dict) -> str:
        query = str(args.get("query", ""))[:200]
        if not query:
            return json.dumps({"error": "empty query"})
        max_r = min(int(args.get("max_results", 6)), 10)
        results = await search_manager_search(query, max_r,
                                              self.include_domains, self.exclude_domains)
        new = []
        for r in results:
            if r.url in self.seen_urls:
                continue
            self.seen_urls.add(r.url)
            repo.upsert_url(self.session_id, r, 1)
            new.append({"url": r.url, "title": r.title[:120],
                        "snippet": r.snippet[:220], "engine": r.engine})
        self.tm.stats["queries_completed"] += 1
        self.tm.stats["pages_discovered"] = len(self.seen_urls)
        repo.log_query(self.session_id, query,
                       "tavily" if results and results[0].engine == "tavily" else "fallback",
                       self.steps_used, len(new))
        self.tm.event("search", {"query": query, "new_results": len(new),
                                 "results": new[:10]})
        return json.dumps({"query": query, "results": new[:10]})

    async def _tool_scrape(self, args: dict) -> str:
        url = str(args.get("url", ""))
        use_js = bool(args.get("use_js", False))
        if self._scraped_count() >= self.max_pages:
            return json.dumps({"error": "page budget reached", "url": url})
        self.tm.set_step("scraping", f"scraping {url}", url)
        page = await scrape_engine.scrape(url, session_id=self.session_id, use_js=use_js)
        self._record_page(page)
        if page.status != "ok":
            # Honest fallback: serve the stored search-engine snippet as a
            # degraded evidence source. It is clearly labelled as snippet-only
            # so downstream extraction treats it as weaker provenance.
            snippet = repo.get_snippet(self.session_id, url)
            if snippet:
                self.tm.event("warning", {
                    "message": f"page blocked ({page.error[:80]}); using search "
                               f"snippet as degraded source for {url}"})
                return json.dumps({
                    "url": url, "status": "snippet_only",
                    "error": page.error,
                    "content_source": "search_snippet (page itself could not be "
                                      "scraped; treat as lower-confidence evidence)",
                    "content": text_block(snippet, 1200),
                    "hint": "extract from this snippet only what it actually "
                            "states; consider searching for an accessible "
                            "alternative source too"})
            return json.dumps({"url": url, "status": page.status, "error": page.error})
        self.tm.set_step("scraping", "page scraped", url)
        return json.dumps({
            "url": url, "final_url": page.final_url, "title": page.title[:150],
            "word_count": page.word_count,
            "content": text_block(page.clean_text or page.raw_text, 4500),
        })

    async def _tool_extract(self, args: dict) -> str:
        url = str(args.get("url", ""))
        fields = [str(f) for f in (args.get("fields") or self.target_fields)][:15]
        if not fields:
            return json.dumps({"error": "no fields specified"})
        if self._record_count() >= self.max_records:
            return json.dumps({"error": "record budget reached"})
        self.tm.set_step("extracting", f"extracting fields from {url}", url)
        page = await self._ensure_page(url, args)
        content = None
        evidence_note = ""
        if page.status == "ok":
            content = page.clean_text or page.raw_text
        else:
            # Degraded provenance: extract only from the stored search snippet,
            # clearly labelled so the record is marked unverified.
            snippet = repo.get_snippet(self.session_id, url)
            if not snippet:
                return json.dumps({"url": url, "status": page.status,
                                   "error": page.error,
                                   "hint": "no content and no snippet for this "
                                           "URL; try web_search for an "
                                           "accessible alternative source"})
            content = snippet
            evidence_note = ("extracted from search snippet only (page blocked: "
                             f"{page.error[:60]}); mark verification 'unverified'")
        rec = await extractor.extract_fields(
            self.prompt, fields, content, url, page.title or "", self.session_id)
        if evidence_note:
            rec.data["notes"] = (rec.data.get("notes", "") + " | " + evidence_note)[:400]
            for f in rec.data["fields"].values():
                if f.get("value") is not None and f.get("verification") == "verified":
                    f["verification"] = "unverified"
                    f["cross_check_evidence"] = (f.get("cross_check_evidence") or "") + \
                        " (downgraded: snippet-only evidence)"
            rec.confidence = min(rec.confidence or 0.4, 0.5)
            rec.verification = "unverified"
        rid = self._save_record(rec, page)
        self.tm.event("record", {"record_id": rid, "url": url,
                                 "entity": rec.data.get("entity_name"),
                                 "fields_filled": sum(
                                     1 for f in rec.data["fields"].values()
                                     if f.get("value") is not None)})
        return json.dumps({"saved_record_id": rid, "url": url,
                           "fields": {k: (v.get("value") if v.get("value") is not None else None)
                                      for k, v in rec.data["fields"].items()},
                           "confidence": rec.confidence,
                           "note": "missing fields are null — do not invent them"})

    async def _tool_finding(self, args: dict) -> str:
        url = str(args.get("url", ""))
        page = await self._ensure_page(url, {})
        if page.status != "ok":
            return json.dumps({"url": url, "status": page.status})
        finding = str(args.get("finding", ""))[:500]
        rec = ExtractedRecord(
            data={
                "entity_name": args.get("topic", "") or page.title[:100],
                "fields": {"finding": {
                    "value": finding, "source_url": url,
                    "evidence": str(args.get("evidence", ""))[:300],
                    "confidence": 0.7, "verification": "unverified"}},
                "sources": [url], "notes": "general finding"},
            url=url, confidence=0.7)
        rid = self._save_record(rec, page)
        self.tm.event("record", {"record_id": rid, "url": url, "entity": finding[:80]})
        return json.dumps({"saved_record_id": rid, "ok": True})

    async def _tool_summarize(self, args: dict) -> str:
        url = str(args.get("url", ""))
        page = await self._ensure_page(url, {})
        if page.status != "ok":
            return json.dumps({"url": url, "status": page.status, "error": page.error})
        summary = await analyzer.summarize_page(self.prompt, page.title,
                                                page.clean_text or page.raw_text,
                                                url, self.session_id)
        self.page_summaries.append({"url": url, "title": page.title, "summary": summary})
        self.tm.event("summary", {"url": url, "summary": summary[:400]})
        return json.dumps({"url": url, "summary": summary})

    async def _tool_verify(self, args: dict) -> str:
        rid = int(args.get("record_id", 0))
        self.tm.set_step("verifying", f"verifying record {rid}")
        records = repo.list_records(self.session_id)
        match = next((r for r in records if r["id"] == rid), None)
        if not match:
            return json.dumps({"error": f"record {rid} not found"})
        rec = ExtractedRecord(data=match["data"], url=match["url"],
                              confidence=match["confidence"],
                              verification=match["verification"])
        rec = await verify_record(rec, self.session_id)
        repo.update_record(rid, rec.confidence, rec.verification, rec.verification_notes)
        verified = sum(1 for f in rec.data.get("fields", {}).values()
                       if f.get("verification") == "verified")
        if rec.verification in ("verified", "partial"):
            self.tm.stats["records_verified"] += 1
        self.tm.event("verification", {"record_id": rid, "status": rec.verification,
                                       "fields_verified": verified})
        return json.dumps({"record_id": rid, "status": rec.verification,
                           "notes": rec.verification_notes})

    # ------------------------------------------------------------- helpers

    async def _ensure_page(self, url: str, args: dict):
        cached = repo.get_page(self.session_id, url)
        if cached and cached["status"] == "ok":
            return _page_from_row(cached)
        if self._scraped_count() >= self.max_pages and not cached:
            return await scrape_engine.scrape(url, session_id=self.session_id,
                                              use_js=bool(args.get("use_js", False)))
        self.tm.set_step("scraping", f"fetching {url}", url)
        page = await scrape_engine.scrape(url, session_id=self.session_id,
                                          use_js=bool(args.get("use_js", False)))
        self._record_page(page)
        return page

    def _record_page(self, page) -> None:
        if page.status == "ok":
            if page.url in self._scraped_urls():
                self.tm.stats["duplicates_skipped"] += 1
                return
            repo.save_page(self.session_id, {
                "url": page.url, "final_url": page.final_url, "status": "ok",
                "title": page.title, "raw_text": page.raw_text,
                "clean_text": page.clean_text, "metadata": page.metadata,
                "content_hash": page.content_hash, "word_count": page.word_count,
                "method": page.metadata.get("provider") or "local",
                "http_status": page.metadata.get("http_status"),
            })
            self.tm.stats["pages_scraped"] += 1
            self.tm.event("page_scraped", {"url": page.url, "title": page.title[:120],
                                           "words": page.word_count,
                                           "method": page.metadata.get("provider") or "local"})
        elif page.status in ("failed", "blocked"):
            self.tm.stats["pages_failed"] += 1
            if not repo.get_page(self.session_id, page.url):
                from ..database.models import SearchResult
                repo.upsert_url(self.session_id,
                                SearchResult(url=page.url, engine="direct"), 1,
                                status=page.status)
            repo.update_url_status(self.session_id, page.url, page.status, page.error[:300])
            self.tm.event("page_failed", {"url": page.url, "status": page.status,
                                          "error": page.error[:200]})
        else:
            self.tm.stats["duplicates_skipped"] += 1
            self.tm.event("page_duplicate", {"url": page.url})

    def _save_record(self, rec: ExtractedRecord, page) -> int:
        dup = any(r["url"] == rec.url and r["data"].get("entity_name") == rec.data.get("entity_name")
                  for r in repo.list_records(self.session_id))
        if dup:
            self.tm.stats["duplicates_skipped"] += 1
            return 0
        rid = repo.save_record(self.session_id, rec, page.content_hash if page else "")
        self.record_ids.append(rid)
        self.tm.stats["records_extracted"] += 1
        return rid

    def _scraped_count(self) -> int:
        return self.tm.stats["pages_scraped"]

    def _record_count(self) -> int:
        return self.tm.stats["records_extracted"]

    def _scraped_urls(self) -> set:
        return {p["url"] for p in repo.list_pages(self.session_id)}

    async def _persist_counts(self) -> None:
        repo.update_session(self.session_id,
                            pages_scraped=self.tm.stats["pages_scraped"],
                            records_found=self.tm.stats["records_extracted"])

    # ------------------------------------------------------------- report

    async def _report(self, plan: dict) -> None:
        records = repo.list_records(self.session_id)
        if not records and not self.page_summaries:
            self.tm.event("warning", {"message": "no content gathered; skipping report"})
            return
        self.tm.set_step("reporting", "writing research report")
        verification_notes = [f"record {r['id']}: {r['verification']} — {r['verification_notes']}"
                              for r in records if r.get("verification_notes")]
        # Fall back to deterministic summaries if the agent never summarized
        if not self.page_summaries:
            pages = repo.list_pages(self.session_id, include_text=True)
            for p in pages[:8]:
                self.page_summaries.append({
                    "url": p["url"], "title": p["title"],
                    "summary": text_block(p.get("clean_text") or "", 400)})
            # still nothing? use stored search snippets as the report's only
            # real collected material (clearly labelled)
            if not self.page_summaries:
                for u in repo.list_urls(self.session_id):
                    if (u["snippet"] or "").strip():
                        self.page_summaries.append({
                            "url": u["url"], "title": u["title"] or u["url"],
                            "summary": "[search snippet only — page could not "
                                       "be scraped] " + u["snippet"][:400]})
        report_md = await analyzer.generate_report(
            self.prompt, plan, [r["data"] | {"source_url": r["url"]} for r in records],
            self.page_summaries, verification_notes, self.session_id)
        report_json = {
            "title": plan.get("objective", "Research Report"),
            "generated_at": repo.utcnow(),
            "records": [r["data"] | {"source_url": r["url"]} for r in records],
            "pages": [{"url": p["url"], "title": p["title"]} for p in repo.list_pages(self.session_id)],
            "queries": repo.list_queries(self.session_id),
        }
        repo.update_session(self.session_id, report_md=report_md,
                            report_json=json.dumps(report_json, ensure_ascii=False))
        self.tm.event("report", {"length": len(report_md)})
        # index into memory
        try:
            await vector_store.index_session(self.session_id)
        except Exception as e:
            log.warning("memory indexing failed: %s", e)

    def _finish(self, status: str, error: str = "") -> None:
        repo.update_session(self.session_id, status=status, error=error)
        self.tm.event("done", {"status": status, "error": error,
                               "stats": self.tm.snapshot()})
        self.tm.set_step(status, error or "research finished")


async def search_manager_search(query: str, max_r: int,
                                include_domains: list[str],
                                exclude_domains: list[str]):
    from ..search.search_manager import search_manager
    return await search_manager.search(
        [query], max_results_per_query=max_r, max_total=max_r,
        include_domains=include_domains or None, exclude_domains=exclude_domains or None)


def _page_from_row(row: dict):
    from ..database.models import PageContent
    return PageContent(
        url=row["url"], final_url=row.get("final_url") or row["url"], status=row["status"],
        title=row.get("title") or "", raw_text=row.get("raw_text") or "",
        clean_text=row.get("clean_text") or "",
        metadata=json.loads(row.get("metadata_json") or "{}"),
        content_hash=row.get("content_hash") or "",
        word_count=row.get("word_count") or 0)


async def start_research(session_id: str, resume: bool = False) -> TaskManager:
    """Create the TaskManager and launch the agent as a background task."""
    from ..agent.task_manager import registry
    tm = await registry.get(session_id)
    agent = ResearchAgent(session_id, tm)
    task = asyncio.create_task(agent.run(resume=resume))
    tm._task = task  # keep a reference; stop() cancels it
    return tm
