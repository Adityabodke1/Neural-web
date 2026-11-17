"""Scraping engine: orchestrates Firecrawl (optional) → HTTPX → Playwright.

Features: robots.txt compliance, page cache (SQLite + memory), duplicate-page
detection via content hash, per-host rate limiting, global concurrency limit,
response size limits, timeouts, and graceful per-URL error handling.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Optional
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings
from ..database.models import PageContent
from .content_extractor import extract_clean
from .firecrawl import firecrawl_scrape
from .html_parser import parse_html
from .playwright_scraper import render as playwright_render
from .url_guard import validate_url

log = logging.getLogger("neuralweb.engine")

robots_lock = asyncio.Lock()
_robots_cache: dict[str, Optional[RobotFileParser]] = {}
_host_next_ok: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}
_sem: Optional[asyncio.Semaphore] = None
_mem_cache: dict[str, PageContent] = {}


def _semaphore() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(settings.MAX_CONCURRENT_SCRAPES)
    return _sem


async def _robots_allows(url: str) -> bool:
    """Check robots.txt for our UA. Network problems default to allow (public content)."""
    host = urlsplit(url).netloc
    async with robots_lock:
        rp = _robots_cache.get(host, "MISS")
        if rp == "MISS":
            rp = None
            try:
                robots_url = urljoin(url, "/robots.txt")
                async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
                    resp = await c.get(robots_url,
                                       headers={"User-Agent": settings.USER_AGENT})
                if resp.status_code == 200 and len(resp.content) < 500_000:
                    rp = RobotFileParser()
                    rp.parse(resp.text.splitlines())
            except Exception as e:
                log.debug("robots.txt unavailable for %s: %s", host, e)
            _robots_cache[host] = rp
    if rp is None:
        return True
    try:
        return rp.can_fetch(settings.USER_AGENT, url)
    except Exception:
        return True


async def _throttle(host: str) -> None:
    lock = _host_locks.setdefault(host, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        wait = _host_next_ok.get(host, 0) - now
        if wait > 0:
            await asyncio.sleep(wait)
        _host_next_ok[host] = time.monotonic() + settings.PER_HOST_DELAY


def _hash_text(text: str) -> str:
    norm = " ".join(text.lower().split())
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


async def _fetch_httpx(url: str) -> tuple[str, str, int]:
    """Fetch raw HTML with httpx. Manual redirect handling: every hop is
    revalidated against the SSRF/private-IP guard (DNS rebinding defence)."""
    from .url_guard import validate_url
    timeout = httpx.Timeout(settings.SCRAPE_TIMEOUT, connect=10.0)
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _hop in range(8):
            current = validate_url(current)  # revalidate every destination
            async with client.stream("GET", current, headers=headers) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "")
                    if not loc:
                        raise httpx.HTTPStatusError(f"redirect without location from {current}",
                                                    request=resp.request, response=resp)
                    from urllib.parse import urljoin
                    current = urljoin(current, loc)
                    continue
                if resp.status_code >= 400:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}",
                                                request=resp.request, response=resp)
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes(65536):
                    size += len(chunk)
                    if size > settings.SCRAPE_MAX_BYTES:
                        log.warning("response from %s exceeds size limit; truncating", url)
                        break
                    chunks.append(chunk)
                return b"".join(chunks).decode(resp.charset_encoding or "utf-8", "ignore"), \
                    str(resp.url), resp.status_code
        raise httpx.HTTPStatusError("too many redirects", request=None, response=None)


async def _parse_page(url: str, html: str, final_url: str, http_status: int,
                      method: str) -> PageContent:
    extracted = extract_clean(html, final_url)
    parsed = extracted["parsed"]
    clean_text = extracted["clean_text"]
    content_hash = _hash_text(clean_text or extracted["raw_text"])
    return PageContent(
        url=url,
        final_url=final_url or url,
        status="ok",
        title=extracted["title"],
        raw_text=extracted["raw_text"][:200_000],
        clean_text=clean_text[:200_000],
        markdown=extracted["markdown"][:200_000],
        metadata={
            **parsed.get("metadata", {}),
            "canonical_url": parsed.get("canonical_url", ""),
            "json_ld": parsed.get("json_ld", []),
            "headings": parsed.get("headings", [])[:40],
            "tables": parsed.get("tables", [])[:10],
            "images": parsed.get("images", [])[:20],
            "internal_links": parsed.get("internal_links", []),
            "external_links": parsed.get("external_links", []),
        },
        links=parsed.get("internal_links", []),
        tables=parsed.get("tables", []),
        content_hash=content_hash,
        word_count=len((clean_text or extracted["raw_text"]).split()),
    )


class ScrapeEngine:
    def __init__(self) -> None:
        self.name = "firecrawl" if settings.has_firecrawl() else "local"

    async def scrape(self, url: str, *, session_id: str = "", use_js: bool = False,
                     force: bool = False) -> PageContent:
        """Scrape one URL. Never raises: failures return PageContent(status='failed')."""
        try:
            url = validate_url(url)
        except Exception as e:
            return PageContent(url=url, status="blocked", error=str(e))

        cache_key = url
        if not force:
            cached = _mem_cache.get(cache_key)
            if cached is not None:
                return cached

        async with _semaphore():
            # ---- Firecrawl (optional provider) ----
            if settings.has_firecrawl():
                try:
                    await _throttle(urlsplit(url).netloc)
                    fc = await firecrawl_scrape(url)
                    if fc:
                        html = fc.get("html") or ""
                        md = fc.get("markdown", "")
                        if md and not html:
                            page = PageContent(
                                url=url, final_url=fc["metadata"].get("url", url),
                                status="ok", title=fc["metadata"].get("title", ""),
                                clean_text=md, markdown=md,
                                metadata={"provider": "firecrawl", **(fc["metadata"] or {})},
                                links=fc.get("links", []), word_count=len(md.split()))
                            _remember(page)
                            return page
                        if html:
                            page = await _parse_page(url, html, fc["metadata"].get("url", url),
                                                     200, "firecrawl")
                            _remember(page)
                            return page
                except Exception as e:
                    log.warning("firecrawl failed for %s (%s); falling back to local", url, e)

            # ---- robots.txt ----
            allowed = await _robots_allows(url)
            if not allowed:
                return PageContent(url=url, status="blocked",
                                   error="disallowed by robots.txt")

            # ---- local fetch: httpx ----
            html, final_url, http_status, method, error = "", "", 0, "httpx", ""
            try:
                await _throttle(urlsplit(url).netloc)
                html, final_url, http_status = await _fetch_httpx(url)
                method = "httpx"
            except Exception as e:
                error = f"httpx: {e}"

            # ---- JS rendering fallback (only when needed/asked) ----
            if (use_js or (html and _looks_js_heavy(html))) and html is not None:
                rendered = await playwright_render(url)
                if rendered and len(rendered) > len(html) * 0.2:
                    html, method = rendered, "playwright"
                    error = ""
            if not html:
                return PageContent(url=url, status="failed", error=error or "empty response")

            try:
                page = await _parse_page(url, html, final_url, http_status, method)
            except Exception as e:
                return PageContent(url=url, status="failed", error=f"parse error: {e}")
            page.metadata["http_status"] = http_status
            _remember(page)
            return page

    async def discover_links(self, page: PageContent, max_links: int = 40,
                             same_site_only: bool = True) -> list[str]:
        base_host = urlsplit(page.final_url).netloc.lower().removeprefix("www.")
        out = []
        for link in page.links[:max_links * 3]:
            try:
                link = validate_url(link)
            except Exception:
                continue
            if same_site_only:
                host = urlsplit(link).netloc.lower().removeprefix("www.")
                if host != base_host:
                    continue
            out.append(link)
            if len(out) >= max_links:
                break
        return out


def _looks_js_heavy(html: str) -> bool:
    text_len = len(html)
    if text_len < 4000:
        return True
    tag_count = html.count("<p") + html.count("<div") + html.count("<article")
    scripts = html.count("<script")
    return scripts > 25 and tag_count < 30


def _remember(page: PageContent) -> None:
    if page.status == "ok":
        if len(_mem_cache) > 400:
            _mem_cache.clear()
        _mem_cache[page.url] = page


scrape_engine = ScrapeEngine()
