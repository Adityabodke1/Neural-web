"""AI analysis: result ranking, page relevance analysis, report generation,
website comparison and the research chat assistant."""
from __future__ import annotations

import json
import logging
from typing import Optional

from .provider import ai_provider, records_block, text_block

log = logging.getLogger("neuralweb.analyzer")

RANK_SCHEMA = {
    "type": "object",
    "required": ["scores"],
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["url", "score"],
                "properties": {"url": {"type": "string"}, "score": {"type": "number"},
                               "reason": {"type": "string"}},
            },
        }
    },
}


class Analyzer:
    async def rank_results(self, prompt: str, results: list[dict],
                           session_id: Optional[str] = None) -> list[dict]:
        """LLM-re-rank search results 0..10 by expected usefulness. Keeps engine score as tiebreak."""
        if not results:
            return []
        listing = [{"url": r["url"], "title": r.get("title", ""), "snippet": text_block(r.get("snippet", ""), 200)}
                   for r in results[:24]]
        data = await ai_provider.chat_json(
            [{"role": "system", "content":
                "You rank web search results for a research request. Score each URL 0-10 for "
                "likelihood of containing the requested information. Official/primary sources, "
                "directories and detailed articles score high; aggregators and generic blogs "
                "score low. Return JSON: {\"scores\": [{\"url\": str, \"score\": number, "
                "\"reason\": str}]} — one entry per input URL."},
             {"role": "user", "content": f"Research request: {prompt}\n\n"
                                         f"Candidates:\n{json.dumps(listing, ensure_ascii=False)}"}],
            request_type="rank", session_id=session_id, schema=RANK_SCHEMA, temperature=0.0)
        scores = {s.get("url"): float(s.get("score", 0)) for s in data.get("scores", [])
                  if isinstance(s, dict)}
        for r in results:
            llm = scores.get(r["url"], 5.0)
            r["llm_score"] = llm
            r["score"] = round(0.65 * llm + 3.5 * min(r.get("engine_score", 0), 1.0), 2)
        return sorted(results, key=lambda r: r["score"], reverse=True)

    async def summarize_page(self, prompt: str, page_title: str, page_text: str, url: str,
                             session_id: Optional[str] = None) -> str:
        return await ai_provider.chat(
            [{"role": "system", "content":
                "Summarize what this page contributes to the research request in 3-5 "
                "sentences. Mention concrete facts only if present in the text. If the page "
                "is irrelevant or empty, say so."},
             {"role": "user", "content":
                 f"Research request: {prompt}\nPage: {url}\nTitle: {page_title}\n\n"
                 f"CONTENT:\n{text_block(page_text, 10000)}"}],
            request_type="summarize", session_id=session_id, max_tokens=400)

    async def generate_report(self, prompt: str, plan: dict, records: list[dict],
                              page_summaries: list[dict], verification_notes: list[str],
                              session_id: Optional[str] = None) -> str:
        sections = "\n\n".join(
            f"### {p.get('title', p.get('url', 'source'))} ({p.get('url', '')})\n{p.get('summary', '')}"
            for p in page_summaries[:30])
        result = await ai_provider.chat(
            [{"role": "system", "content":
                "You are a research report writer. Produce a complete Markdown report with "
                "these sections: # Title, ## Executive Summary, ## Key Findings (bulleted, "
                "each fact attributed with its source URL inline), ## Detailed Findings, "
                "## Data Table (if tabular data exists — Markdown table), ## Verification "
                "& Caveats, ## Sources (numbered list of all source URLs used). "
                "GROUNDING RULES: use ONLY facts contained in the extracted records and "
                "page summaries below — never add facts from your own knowledge. If the "
                "provided material does not answer part of the request, state explicitly "
                "that the research did not gather it. Snippet-only records (marked "
                "'snippet-only' or verification 'unverified') must be labelled as "
                "lower-confidence. NEVER present a claim without a source URL."},
             {"role": "user", "content":
                 f"Research request: {prompt}\n\nPlan objective: {plan.get('objective', '')}\n"
                 f"Target fields: {plan.get('target_fields', [])}\n\n"
                 f"Extracted records (with per-field source URLs):\n{records_block(records, 80)}\n\n"
                 f"Page summaries:\n{sections}\n\n"
                 f"Verification notes:\n" + "\n".join(verification_notes[:20])}],
            request_type="report", session_id=session_id, max_tokens=4096)
        return result

    async def compare_sites(self, urls_and_text: list[dict], aspect: str,
                            session_id: Optional[str] = None) -> str:
        pages = "\n\n".join(
            f"### {p['url']}\n{text_block(p['text'], 6000)}" for p in urls_and_text)
        return await ai_provider.chat(
            [{"role": "system", "content":
                "You compare websites on the requested aspect. Produce a Markdown comparison "
                "with a table (rows = items compared, columns = the sites) and short analysis. "
                "Every cell with factual content must cite the site URL it came from. "
                "If a site lacks the information, write 'not found on page'."},
             {"role": "user", "content": f"Aspect to compare: {aspect}\n\nPAGES:\n{pages}"}],
            request_type="compare", session_id=session_id, max_tokens=3000)

    async def answer_question(self, question: str, context_records: list[dict],
                              context_pages: list[dict], session_id: Optional[str] = None) -> str:
        recs = records_block(context_records, 40)
        pages = "\n\n".join(
            f"### {p.get('title', '')} ({p.get('url', '')})\n{text_block(p.get('clean_text', ''), 4000)}"
            for p in context_pages[:12])
        return await ai_provider.chat(
            [{"role": "system", "content":
                "You are the NEURAL WEB research assistant. Answer the user's question about "
                "their collected research. STRICTLY:\n"
                "- Facts from sources: cite the source URL inline.\n"
                "- Your own analysis: prefix with 'Analysis:' and cite nothing.\n"
                "- Missing information: say what was not found in the collected data.\n"
                "- Never invent source URLs or citations.\n"
                "Use Markdown."},
             {"role": "user", "content":
                 f"Question: {question}\n\nExtracted records:\n{recs}\n\nRelevant pages:\n{pages}"}],
            request_type="ask", session_id=session_id, max_tokens=2000)


analyzer = Analyzer()
