"""Tavily Search API client (REST via httpx, no extra SDK dependency)."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from ..config import settings
from ..database.models import SearchResult

log = logging.getLogger("neuralweb.tavily")

TAVILY_URL = "https://api.tavily.com/search"


async def tavily_search(query: str, max_results: int = 8,
                        include_domains: Optional[list[str]] = None,
                        exclude_domains: Optional[list[str]] = None,
                        search_depth: str = "basic") -> list[SearchResult]:
    if not settings.has_tavily():
        return []
    payload: dict = {
        "api_key": settings.TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(TAVILY_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Tavily search failed for %r: %s", query, e)
        return []
    out = []
    for r in data.get("results", []):
        out.append(SearchResult(
            url=r.get("url", ""),
            title=r.get("title", ""),
            snippet=r.get("content", ""),
            engine="tavily",
            score=float(r.get("score", 0) or 0),
            published=r.get("published_date"),
        ))
    return out
