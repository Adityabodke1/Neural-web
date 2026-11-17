"""Change detection: snapshot pages over time and produce readable diffs."""
from __future__ import annotations

import difflib
import logging
import re

from ..database import repository as repo
from ..scraping.engine import scrape_engine

log = logging.getLogger("neuralweb.monitor")

_PRICE_RE = re.compile(r"([$€£₹]\s?\d[\d,.]*)")


def _extract_changes(old: str, new: str, context: int = 1) -> dict:
    old_lines = [l.strip() for l in old.splitlines() if l.strip()]
    new_lines = [l.strip() for l in new.splitlines() if l.strip()]
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    added, removed, changed = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "insert":
            added += new_lines[j1:j2]
        elif tag == "delete":
            removed += old_lines[i1:i2]
        elif tag == "replace":
            changed += [{"old": o, "new": n}
                        for o, n in zip(old_lines[i1:i2], new_lines[j1:j2])]
            if (i2 - i1) > (j2 - j1):
                removed += old_lines[i1 + (j2 - j1):i2]
            elif (j2 - j1) > (i2 - i1):
                added += new_lines[j1 + (i2 - i1):j2]
    prices_old = set(_PRICE_RE.findall(old))
    prices_new = set(_PRICE_RE.findall(new))
    return {
        "added": added[:40],
        "removed": removed[:40],
        "changed": changed[:40],
        "prices_removed": sorted(prices_old - prices_new)[:20],
        "prices_added": sorted(prices_new - prices_old)[:20],
        "similarity": round(difflib.SequenceMatcher(None, "\n".join(old_lines),
                                                    "\n".join(new_lines)).ratio(), 4),
    }


async def check_page_for_changes(url: str, session_id: str = "") -> dict:
    """Fetch the page, compare with the last stored snapshot, store the new one."""
    page = await scrape_engine.scrape(url, session_id=session_id, force=True)
    if page.status != "ok":
        return {"url": url, "status": page.status, "error": page.error}
    current_text = page.clean_text or page.raw_text
    prior = repo.latest_snapshot(url)
    repo.save_snapshot({"url": url, "content_hash": page.content_hash,
                        "clean_text": current_text, "word_count": page.word_count,
                        "title": page.title})
    result: dict = {
        "url": url,
        "title": page.title,
        "final_url": page.final_url,
        "first_check": prior is None,
        "snapshots_stored": repo.count_snapshots(url),
        "word_count": page.word_count,
    }
    if prior is None:
        result["change_detected"] = False
        result["note"] = "first snapshot stored; run this command again later to detect changes"
        return result
    if prior["content_hash"] == page.content_hash:
        result["change_detected"] = False
        result["note"] = "page content is unchanged since the last check"
        return result
    diff = _extract_changes(prior["snapshot_text"], current_text)
    result["change_detected"] = True
    result["previous_captured_at"] = prior["captured_at"]
    result["similarity"] = diff.pop("similarity")
    result["changes"] = diff
    return result
