"""Optional Firecrawl API provider (https://firecrawl.dev, v1 REST API).

If FIRECRAWL_API_KEY is not set, the local engine handles everything instead.
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("neuralweb.firecrawl")

FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"


async def firecrawl_scrape(url: str, timeout: float = 45.0) -> dict | None:
    """Return {'markdown', 'html', 'metadata', 'links'} or None."""
    if not settings.has_firecrawl():
        return None
    headers = {
        "Authorization": f"Bearer {settings.FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "url": url,
        "formats": ["markdown", "html", "links", "metadata"],
        "onlyMainContent": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(FIRECRAWL_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("firecrawl scrape failed for %s: %s", url, e)
        return None
    if not data.get("success"):
        return None
    doc = data.get("data", {})
    return {
        "markdown": doc.get("markdown", ""),
        "html": doc.get("html", ""),
        "metadata": doc.get("metadata", {}) or {},
        "links": doc.get("links", []) or [],
    }
