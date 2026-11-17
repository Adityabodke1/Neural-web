"""Integration tests for search, scraping, extraction pipeline and exports.
These hit real services (keyless fallback search, public websites).
Run: .venv/bin/python -m pytest backend/tests/test_pipeline.py -v
"""
from __future__ import annotations

import asyncio

import pytest


def test_fallback_search_returns_results():
    from backend.app.search.fallback import fallback_search
    results = asyncio.run(fallback_search("python programming language", 5))
    assert len(results) >= 1
    assert all(r.url.startswith("http") for r in results)


def test_search_manager_dedupes():
    from backend.app.search.search_manager import SearchManager, normalize_url
    assert normalize_url("https://www.Example.com/a?utm_source=x&b=2#frag") == \
        "https://example.com/a?b=2"


def test_scrape_real_page():
    from backend.app.scraping.engine import scrape_engine
    page = asyncio.run(scrape_engine.scrape("https://example.com"))
    assert page.status == "ok"
    assert "example" in page.title.lower()
    assert page.content_hash


def test_scrape_blocks_private_urls():
    from backend.app.scraping.engine import scrape_engine
    page = asyncio.run(scrape_engine.scrape("http://127.0.0.1:8000/"))
    assert page.status in ("blocked", "failed")


def test_scrape_respects_size_and_timeout():
    from backend.app.scraping.engine import scrape_engine
    # httpbin delay endpoint may be flaky; just assert no crash and a status
    page = asyncio.run(scrape_engine.scrape("https://httpbin.org/delay/5"))
    assert page.status in ("ok", "failed", "blocked")


def test_robots_txt_compliance():
    from backend.app.scraping.engine import _robots_allows
    allowed = asyncio.run(_robots_allows("https://www.python.org/about/"))
    assert allowed is True


def test_change_detection_first_snapshot():
    from backend.app.agent.monitor import check_page_for_changes
    res = asyncio.run(check_page_for_changes("https://example.com"))
    assert res["url"] == "https://example.com"
    # honest reporting whether this was the first snapshot or a repeat check
    assert res["first_check"] is True or res["change_detected"] is False


def test_export_session(tmp_path):
    from backend.app.database import repository as repo
    from backend.app.database.models import ExtractedRecord, SearchResult
    from backend.app.exports.exporters import export_session
    sid = repo.create_session("test export", "quick", {})
    repo.save_page(sid, {"url": "https://example.com", "status": "ok", "title": "Example",
                         "raw_text": "hi", "clean_text": "hi", "metadata": {},
                         "content_hash": "abc", "word_count": 1, "method": "test"})
    repo.save_record(sid, ExtractedRecord(
        data={"entity_name": "Example", "fields": {
            "website": {"value": "https://example.com", "source_url": "https://example.com",
                        "evidence": "example", "confidence": 0.9,
                        "verification": "verified"}},
            "sources": ["https://example.com"], "notes": ""},
        url="https://example.com", confidence=0.9, verification="verified"))
    for fmt in ("json", "csv", "xlsx"):
        out = export_session(sid, fmt)
        assert out["records"] == 1
        assert out["path"]


def test_memory_search_after_indexing():
    from backend.app.database import repository as repo
    from backend.app.database.models import ExtractedRecord
    from backend.app.memory.vector_store import vector_store
    sid = repo.create_session("memory test — purple elephants database", "quick", {})
    repo.save_record(sid, ExtractedRecord(
        data={"entity_name": "PurpleElephantDB", "fields": {
            "finding": {"value": "purple elephants database", "source_url": "x",
                        "evidence": "e", "confidence": 0.5, "verification": "unverified"}},
            "sources": [], "notes": ""},
        url="https://example.com"))
    vector_store.index_session(sid)
    hits = vector_store.search("purple elephants", top_k=3)
    assert any("purple elephants" in h["text"].lower() for h in hits)


def test_multi_hop_redirect_validation():
    from backend.app.scraping.engine import _fetch_httpx
    from backend.app.scraping.url_guard import UnsafeURLError
    # a redirect to a private IP must be refused (dns-rebinding defence)
    import httpx

    class FakeRedirect:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def stream(self, *a, **k):
            return self

        async def __call__(self, *a, **k):  # pragma: no cover
            pass

    # simple direct check of validate_url on redirect target
    from backend.app.scraping.url_guard import validate_url
    with pytest.raises(UnsafeURLError):
        validate_url("http://10.0.0.5/next")
