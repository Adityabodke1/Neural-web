"""Scraping / crawling / monitoring API."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agent.monitor import check_page_for_changes
from ..database import repository as repo
from ..scraping.engine import scrape_engine
from ..scraping.url_guard import UnsafeURLError, validate_url

router = APIRouter(prefix="/api/scrape", tags=["scraping"])


class ScrapeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    use_js: bool = False


class CrawlRequest(BaseModel):
    url: str
    depth: int = Field(default=1, ge=1, le=3)
    max_pages: int = Field(default=5, ge=1, le=40)


@router.post("")
async def scrape(req: ScrapeRequest):
    try:
        validate_url(req.url)
    except UnsafeURLError as e:
        raise HTTPException(422, f"unsafe URL: {e}")
    page = await scrape_engine.scrape(req.url, use_js=req.use_js)
    if page.status == "ok":
        sid = repo.create_session(f"[scrape] {req.url}", "quick", {})
        repo.save_page(sid, {
            "url": page.url, "final_url": page.final_url, "status": "ok",
            "title": page.title, "raw_text": page.raw_text[:50_000],
            "clean_text": page.clean_text[:100_000], "metadata": page.metadata,
            "content_hash": page.content_hash, "word_count": page.word_count,
            "method": page.metadata.get("provider") or "local",
            "http_status": page.metadata.get("http_status"),
        })
        repo.update_session(sid, status="complete", pages_scraped=1)
    return {
        "url": page.url, "final_url": page.final_url, "status": page.status,
        "title": page.title, "word_count": page.word_count,
        "content_hash": page.content_hash,
        "error": page.error,
        "content": (page.clean_text or page.raw_text)[:20_000],
        "markdown": (page.markdown or "")[:20_000],
        "metadata": {k: v for k, v in page.metadata.items()
                     if k in ("canonical_url", "json_ld", "headings", "tables",
                              "images", "http_status", "provider")},
        "internal_links": page.links[:60],
        "tables": page.tables[:5],
    }


@router.post("/crawl")
async def crawl(req: CrawlRequest):
    """BFS crawl within one domain (robots.txt compliant, dedup, depth-limited)."""
    try:
        validate_url(req.url)
    except UnsafeURLError as e:
        raise HTTPException(422, f"unsafe URL: {e}")
    start_host = urlsplit(req.url).netloc.lower().removeprefix("www.")
    sid = repo.create_session(f"[crawl] {req.url}", "quick",
                              {"depth": req.depth, "max_pages": req.max_pages})
    seen: set[str] = set()
    frontier: list[tuple[str, int]] = [(req.url, 0)]
    results = []
    while frontier and len(seen) < req.max_pages:
        url, depth = frontier.pop(0)
        if url in seen or depth > req.depth:
            continue
        seen.add(url)
        page = await scrape_engine.scrape(url, session_id="crawl")
        if page.status != "ok":
            results.append({"url": url, "status": page.status, "error": page.error[:200],
                            "depth": depth})
            continue
        repo.save_page(sid, {
            "url": page.url, "final_url": page.final_url, "status": "ok",
            "title": page.title, "raw_text": page.raw_text[:30_000],
            "clean_text": page.clean_text[:50_000], "metadata": page.metadata,
            "content_hash": page.content_hash, "word_count": page.word_count,
            "method": page.metadata.get("provider") or "local"})
        results.append({"url": page.url, "title": page.title, "words": page.word_count,
                        "depth": depth, "status": "ok",
                        "links": page.links[:20]})
        if depth < req.depth:
            for link in page.links[:40]:
                try:
                    link = validate_url(link)
                except UnsafeURLError:
                    continue
                if urlsplit(link).netloc.lower().removeprefix("www.") == start_host \
                        and link not in seen:
                    frontier.append((link, depth + 1))
    return {"start": req.url, "pages": results, "crawled": len(seen)}


class MonitorRequest(BaseModel):
    url: str


@router.post("/monitor")
async def monitor(req: MonitorRequest):
    try:
        return await check_page_for_changes(req.url, session_id="monitor")
    except UnsafeURLError as e:
        raise HTTPException(422, f"unsafe URL: {e}")
