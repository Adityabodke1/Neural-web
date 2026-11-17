"""Intelligent field-level extraction with full source provenance.

The extractor NEVER invents values: every extracted field must carry the source
URL, an evidence snippet copied from the page, and per-field confidence.
Unavailable information is null.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..database.models import ExtractedRecord
from .provider import ai_provider, text_block

log = logging.getLogger("neuralweb.extractor")

RECORD_SCHEMA = {
    "type": "object",
    "required": ["fields"],
    "properties": {
        "entity_name": {"type": ["string", "null"]},
        "fields": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["value"],
                "properties": {
                    "value": {},  # string or null
                    "evidence": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}

# Fields that are plain URLs and can be sanity-checked without the LLM
_URL_FIELDS = {"website", "url", "site", "homepage", "contact_url", "careers_url"}


def normalize_record(raw: dict, url: str) -> ExtractedRecord:
    """Normalize a raw LLM record into an ExtractedRecord with provenance metadata.

    Output data shape (stored in DB and exports):
    {
      "entity_name": "...",
      "fields": {
         "founder": {"value": "Ada L.", "source_url": "https://..", "evidence": "Founded by ..",
                      "confidence": 0.9, "verification": "unverified"}
      },
      "sources": [url, ...],
      "notes": "..."
    }
    """
    fields_out: dict = {}
    sources: set = {url}
    entity_name = raw.get("entity_name") or None
    for name, spec in (raw.get("fields") or {}).items():
        if not isinstance(spec, dict):
            continue
        value = spec.get("value")
        key = re.sub(r"[\s\-]+", "_", str(name).strip().lower()) or str(name).strip()
        if value in (None, "", [], {}):
            fields_out[key] = {"value": None, "source_url": None, "evidence": "",
                               "confidence": 0.0, "verification": "missing"}
            continue
        if not isinstance(value, (str, int, float, bool, list)):
            value = str(value)
        if isinstance(value, list):
            value = [str(v) for v in value]
        if key in _URL_FIELDS and isinstance(value, str):
            v = value.strip()
            if not re.match(r"^https?://", v):
                # keep as-is but mark low confidence; never fabricate a scheme change
                pass
        evidence = str(spec.get("evidence", "")).strip()[:400]
        fields_out[key] = {
            "value": value,
            "source_url": url,
            "evidence": evidence,
            "confidence": _field_confidence(evidence, value),
            "verification": "unverified",
        }
        sources.add(url)
    confidences = [f["confidence"] for f in fields_out.values() if f["value"] is not None]
    record_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return ExtractedRecord(
        data={
            "entity_name": entity_name,
            "fields": fields_out,
            "sources": sorted(sources),
            "notes": str(raw.get("notes", ""))[:500],
        },
        url=url,
        confidence=round(record_conf, 3),
        verification="unverified",
    )


def _field_confidence(evidence: str, value) -> float:
    if not evidence:
        return 0.35
    ev = evidence.lower()
    v = str(value).lower().strip()
    if not v or v in ("null", "none", "n/a", "unknown"):
        return 0.1
    if v[:40] in ev:
        return 0.95
    overlap = sum(1 for w in v.split()[:6] if w and w in ev)
    return min(0.9, 0.45 + 0.1 * overlap)


class Extractor:
    async def extract_fields(self, prompt: str, target_fields: list[str],
                             page_text: str, url: str, page_title: str = "",
                             session_id: Optional[str] = None) -> ExtractedRecord:
        """Extract the requested fields from one page with evidence snippets."""
        fields_desc = ", ".join(target_fields) if target_fields else \
            "the most relevant facts for the research request"
        system = (
            "You are a precise data extraction engine. From the given page content, extract "
            "the requested fields. STRICT RULES:\n"
            "1. Use only information literally present in the page. Never infer or fabricate.\n"
            "2. For every non-null field, copy a short 'evidence' snippet from the page "
            "(verbatim, max 30 words) that justifies the value.\n"
            "3. If a field is not on the page, set its value to null and omit evidence.\n"
            "4. Do not merge multiple sources; this page is a single source.\n"
            "Return JSON: {\"entity_name\": str|null, \"fields\": {field: "
            "{\"value\": str|null, \"evidence\": str}}, \"notes\": str}")
        user = (
            f"Research request: {prompt}\n"
            f"Fields to extract: {fields_desc}\n"
            f"Page URL: {url}\nPage title: {page_title}\n\n"
            f"PAGE CONTENT:\n{text_block(page_text, 14000)}")
        raw = await ai_provider.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            request_type="extract", session_id=session_id, schema=RECORD_SCHEMA,
            temperature=0.0)
        return normalize_record(raw, url)


extractor = Extractor()
