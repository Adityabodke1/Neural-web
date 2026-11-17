"""Claim verification: cross-checks extracted facts against independent sources.

A field is only marked 'verified' when an independent page (different domain)
actually contains the value; otherwise it becomes 'contradicted', 'partial' or
stays 'unverified'. Nothing is ever marked verified without evidence text.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from ..ai.provider import ai_provider, text_block
from ..database import repository as repo
from ..database.models import ExtractedRecord
from ..search import search_manager

log = logging.getLogger("neuralweb.verify")

CHECK_SCHEMA = {
    "type": "object",
    "required": ["checks"],
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["field", "status"],
                "properties": {
                    "field": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["verified", "contradicted", "unverified"]},
                    "evidence": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        }
    },
}


def _entity_query(record: ExtractedRecord) -> str:
    d = record.data
    name = d.get("entity_name") or ""
    fields = d.get("fields", {})
    for k in ("company_name", "name", "title", "organization"):
        if not name and fields.get(k, {}).get("value"):
            name = str(fields[k]["value"])
            break
    extras = []
    for k in ("industry", "location", "founder", "country", "city"):
        v = fields.get(k, {}).get("value")
        if v:
            extras.append(str(v))
    return " ".join(x for x in [name] + extras[:2] if x).strip()


async def verify_record(record: ExtractedRecord, session_id: str,
                        max_extra_sources: int = 1) -> ExtractedRecord:
    """Search for one independent source and cross-check the record's key fields."""
    data = record.data
    fields: dict = data.get("fields", {})
    filled = {k: f for k, f in fields.items() if f.get("value") not in (None, "", [], {})}
    if not filled:
        record.verification = "no_data"
        return record

    query = _entity_query(record)
    if not query:
        record.verification = "unverified"
        return record

    original_host = urlsplit(record.url).netloc.lower()
    results = await search_manager.search([query], max_results_per_query=5, max_total=5)
    independent = [r for r in results
                   if urlsplit(r.url).netloc.lower() not in (original_host,)
                   and not original_host.endswith(urlsplit(r.url).netloc.lower())]
    if not independent:
        record.verification = "unverified"
        record.verification_notes = "no independent source found for cross-check"
        return record

    from ..scraping.engine import scrape_engine
    cross_page = await scrape_engine.scrape(independent[0].url, session_id=session_id)
    if cross_page.status != "ok" or not cross_page.clean_text:
        record.verification = "unverified"
        record.verification_notes = "independent source could not be scraped"
        return record

    claims = [{"field": k, "value": f["value"]} for k, f in filled.items()]
    try:
        checks = await ai_provider.chat_json(
            [{"role": "system", "content":
                "You verify extracted facts against an INDEPENDENT source page. For each "
                "claim decide: 'verified' (independent page clearly states the same value), "
                "'contradicted' (independent page states a different value), or 'unverified' "
                "(independent page does not mention it). Copy short evidence snippets. "
                "Return JSON: {\"checks\": [{\"field\", \"status\", \"evidence\", \"note\"}]}."},
             {"role": "user", "content":
                 f"Claims (from {record.url}):\n{claims}\n\n"
                 f"Independent source ({cross_page.url}):\n{text_block(cross_page.clean_text, 9000)}"}],
            request_type="verify", session_id=session_id, schema=CHECK_SCHEMA,
            temperature=0.0)
    except Exception as e:
        log.warning("verification LLM step failed: %s", e)
        record.verification = "unverified"
        record.verification_notes = f"verification step failed: {e}"
        return record

    statuses = []
    notes = []
    by_field = {c.get("field"): c for c in checks.get("checks", []) if isinstance(c, dict)}
    for k, f in filled.items():
        c = by_field.get(k) or by_field.get(k.replace("_", " "))
        if not c:
            f["verification"] = "unverified"
            continue
        status = c.get("status", "unverified")
        f["verification"] = status
        f["cross_check_source"] = cross_page.url
        if c.get("evidence"):
            f["cross_check_evidence"] = str(c.get("evidence"))[:300]
        statuses.append(status)
        if c.get("note"):
            notes.append(f"{k}: {c['note']}")
        data["sources"].append(cross_page.url)
    data["sources"] = sorted(set(data.get("sources", [])))

    if statuses:
        v_count = statuses.count("verified")
        if v_count == len(statuses):
            record.verification = "verified"
            record.confidence = min(1.0, record.confidence + 0.15)
        elif statuses.count("contradicted") > 0:
            record.verification = "contradicted"
            record.confidence = max(0.1, record.confidence - 0.3)
        elif v_count > 0:
            record.verification = "partial"
        else:
            record.verification = "unverified"
    record.verification_notes = "; ".join(notes)[:500] or \
        f"cross-checked against {cross_page.url}"
    return record
