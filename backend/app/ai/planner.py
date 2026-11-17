"""AI research planner: understands the request, builds a plan, generates
targeted search queries and identifies information gaps."""
from __future__ import annotations

import logging
from typing import Optional

from .provider import ai_provider, records_block, text_block

log = logging.getLogger("neuralweb.planner")

PLAN_SCHEMA = {
    "type": "object",
    "required": ["restated_request", "objective", "sub_questions", "strategy"],
    "properties": {
        "restated_request": {"type": "string"},
        "objective": {"type": "string"},
        "sub_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "target_fields": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "entity_type": {"type": "string"},
        "strategy": {"type": "string"},
    },
}

QUERIES_SCHEMA = {
    "type": "object",
    "required": ["queries"],
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1, "maxItems": 12,
        }
    },
}

GAPS_SCHEMA = {
    "type": "object",
    "required": ["missing_fields", "additional_queries"],
    "properties": {
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "additional_queries": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "enough_information": {"type": "boolean"},
    },
}

DEPTH_CONFIG = {
    "quick":    {"max_queries": 3, "max_rounds": 1},
    "standard": {"max_queries": 6, "max_rounds": 2},
    "deep":     {"max_queries": 10, "max_rounds": 4},
}


class Planner:
    async def build_plan(self, prompt: str, session_id: Optional[str] = None) -> dict:
        messages = [
            {"role": "system", "content":
                "You are a research planner. Given a user's research request, restate it "
                "precisely, define the objective, list 3-6 sub-questions that must be answered, "
                "and list target fields ONLY if the user asks for specific data fields "
                "(e.g. company name, website, founder...). Be concise."},
            {"role": "user", "content": f"Research request:\n{prompt}\n\nReturn JSON."},
        ]
        return await ai_provider.chat_json(
            messages, request_type="plan", session_id=session_id, schema=PLAN_SCHEMA)

    async def generate_queries(self, prompt: str, plan: dict, count: int,
                               session_id: Optional[str] = None) -> list[str]:
        system = (
            "You generate effective web search queries. Produce diverse, specific queries "
            "covering different angles of the research request. Mix broad and narrow phrasing, "
            "include likely site names or regions when helpful. No queries longer than 10 words. "
            "Return JSON: {\"queries\": [...]}")
        user = (
            f"Research request: {prompt}\n\n"
            f"Objective: {plan.get('objective', '')}\n"
            f"Sub-questions: {plan.get('sub_questions', [])}\n\n"
            f"Generate exactly {count} distinct search queries.")
        data = await ai_provider.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            request_type="queries", session_id=session_id, schema=QUERIES_SCHEMA)
        queries = [q.strip() for q in data.get("queries", []) if isinstance(q, str) and q.strip()]
        return queries[:count]

    async def identify_gaps(self, prompt: str, plan: dict, records: list[dict],
                            pages_scraped: int, session_id: Optional[str] = None) -> dict:
        """Given current findings, decide what is missing and what to search next."""
        fields = plan.get("target_fields", [])
        user = (
            f"Research request: {prompt}\n"
            f"Target fields: {fields or '(general research — no specific fields)'}\n"
            f"Pages scraped so far: {pages_scraped}\n\n"
            f"Extracted records so far:\n{records_block(records)}\n\n"
            "Analyse coverage. If specific fields were requested, note which are still "
            "missing or sparse. Suggest up to 5 additional, DIFFERENT search queries to fill "
            "the gaps. Set enough_information=true if coverage is sufficient to answer the "
            "request. Return JSON.")
        return await ai_provider.chat_json(
            [{"role": "system", "content":
                "You audit research coverage and propose follow-up searches. Return JSON: "
                "{\"missing_fields\": [...], \"additional_queries\": [...], "
                "\"enough_information\": bool}"},
             {"role": "user", "content": user}],
            request_type="gaps", session_id=session_id, schema=GAPS_SCHEMA)


planner = Planner()
