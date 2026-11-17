"""Search manager: multi-query search, engine selection, dedup, domain filters,
and ranking. All search goes through here."""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from ..config import settings
from ..database.models import SearchResult
from . import fallback, tavily

log = logging.getLogger("neuralweb.search")

_TRACK_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                 "gclid", "fbclid", "ref", "ref_src"}


def normalize_url(url: str) -> str:
    """Canonical form used for dedup: lowercase host, strip fragment + trackers."""
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme:
            parts = parts._replace(scheme="https")
        host = parts.netloc.lower().removeprefix("www.")
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in _TRACK_PARAMS]
        return urlunsplit((parts.scheme, host, parts.path or "/", urlencode(query), ""))
    except Exception:
        return url


def host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def domain_matches(url: str, domains: list[str]) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)


class SearchManager:
    def __init__(self) -> None:
        self.engine_name = "tavily" if settings.has_tavily() else "fallback(bing+ddg+wikipedia)"

    async def search(self, queries: list[str], *, max_results_per_query: int = 6,
                     include_domains: list[str] | None = None,
                     exclude_domains: list[str] | None = None,
                     max_total: int = 24) -> list[SearchResult]:
        """Run all queries concurrently, dedupe, filter and rank."""
        sem = asyncio.Semaphore(3)

        async def run_one(q: str) -> list[SearchResult]:
            async with sem:
                if settings.has_tavily():
                    results = await tavily.tavily_search(
                        q, max_results_per_query,
                        include_domains=include_domains or None,
                        exclude_domains=exclude_domains or None)
                    if not results:  # tavily hiccup → fallback for this query
                        results = await fallback.fallback_search(q, max_results_per_query)
                else:
                    results = await fallback.fallback_search(q, max_results_per_query)
                return results

        batches = await asyncio.gather(*[run_one(q) for q in queries],
                                       return_exceptions=True)
        merged: dict[str, SearchResult] = {}
        for batch in batches:
            if isinstance(batch, Exception):
                log.warning("search batch failed: %s", batch)
                continue
            for r in batch:
                if not r.url or not r.url.startswith(("http://", "https://")):
                    continue
                key = normalize_url(r.url)
                if key in merged:
                    merged[key].score = max(merged[key].score, r.score)
                else:
                    r.url = key
                    merged[key] = r
        results = list(merged.values())
        if include_domains:
            results = [r for r in results if domain_matches(r.url, include_domains)]
        if exclude_domains:
            results = [r for r in results if not domain_matches(r.url, exclude_domains)]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:max_total]

    @staticmethod
    def dedupe_against(results: list[SearchResult], seen_urls: set[str]) -> list[SearchResult]:
        return [r for r in results if r.url not in seen_urls]


search_manager = SearchManager()
