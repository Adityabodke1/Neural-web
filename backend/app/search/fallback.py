"""Keyless fallback search: DuckDuckGo HTML endpoint + Wikipedia API.

Used automatically when TAVILY_API_KEY is not configured (or when Tavily fails).
Only public, legally accessible search interfaces are used; no CAPTCHA or
anti-bot bypass is attempted — if a source blocks us, we return partial results.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from ..database.models import SearchResult

log = logging.getLogger("neuralweb.fallback")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 NeuralWebResearch/1.0")
WIKI_UA = "NeuralWebResearch/1.0 (autonomous research agent; respects robots; contact: neuralweb@localhost)"


async def duckduckgo_search(query: str, max_results: int = 8,
                            client: Optional[httpx.AsyncClient] = None) -> list[SearchResult]:
    url = "https://html.duckduckgo.com/html/"
    data = {"q": query}
    headers = {"User-Agent": UA, "Referer": "https://html.duckduckgo.com/"}
    try:
        own = client is None
        c = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        resp = await c.post(url, data=data, headers=headers)
        html = resp.text
        if own:
            await c.aclose()
    except Exception as e:
        log.warning("DuckDuckGo search failed for %r: %s", query, e)
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[SearchResult] = []
    for block in soup.select("div.result"):
        a = block.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        link = a["href"]
        if link.startswith("//"):
            link = "https:" + link
        if "duckduckgo.com/l/" in link or "uddg=" in link:
            try:
                link = unquote(parse_qs(urlsplit(link).query)["uddg"][0])
            except Exception:
                continue
        snippet_el = block.select_one("td.result__snippet, .result__snippet")
        out.append(SearchResult(
            url=link,
            title=a.get_text(" ", strip=True),
            snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
            engine="duckduckgo",
            score=max(0.0, 1.0 - len(out) * 0.05),
        ))
        if len(out) >= max_results:
            break
    return out


def _decode_bing_link(link: str) -> str:
    """Resolve Bing /ck/a redirect links to the real target URL (client-side
    decode of the u=a1<base64> parameter — no redirect is followed server-side)."""
    if "bing.com/ck/a" not in link:
        return link if link.startswith("http") else ""
    try:
        q = parse_qs(urlsplit(link).query)
        u = q.get("u", [""])[0]
        if u.startswith("a1"):
            u = u[2:]
        u = u + "=" * (-len(u) % 4)          # restore stripped base64 padding
        target = base64.urlsafe_b64decode(u).decode("utf-8", "ignore")
        return target if target.startswith("http") else ""
    except Exception:
        return ""


async def bing_search(query: str, max_results: int = 8) -> list[SearchResult]:
    """Bing HTML results (server-rendered, no JS, no captcha for normal queries)."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": UA,
                                              "Accept-Language": "en-US,en;q=0.9"}) as c:
            resp = await c.get("https://www.bing.com/search", params={"q": query})
    except Exception as e:
        log.warning("Bing search failed for %r: %s", query, e)
        return []
    if resp.status_code != 200:
        log.warning("Bing search returned HTTP %s for %r", resp.status_code, query)
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    out: list[SearchResult] = []
    for block in soup.select("li.b_algo"):
        a = block.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        link = _decode_bing_link(str(a["href"]))
        if not link:
            continue
        snippet_el = block.select_one(".b_caption p, p")
        out.append(SearchResult(
            url=link,
            title=a.get_text(" ", strip=True),
            snippet=snippet_el.get_text(" ", strip=True) if snippet_el else "",
            engine="bing",
            score=max(0.0, 0.9 - len(out) * 0.04),
        ))
        if len(out) >= max_results:
            break
    return out


async def wikipedia_search(query: str, max_results: int = 4) -> list[SearchResult]:
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
           f"&srlimit={max_results}&srsearch={quote(query)}")
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": WIKI_UA}) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("Wikipedia search failed for %r: %s", query, e)
        return []
    out = []
    for hit in data.get("query", {}).get("search", []):
        title = hit.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
        out.append(SearchResult(
            url=f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            title=title,
            snippet=snippet,
            engine="wikipedia",
            score=0.6,
        ))
    return out


async def fallback_search(query: str, max_results: int = 8) -> list[SearchResult]:
    """Run all keyless engines concurrently and merge. Bing (server-rendered)
    is tried first since it is the most reliably accessible without a key;
    DDG/Wikipedia may rate-limit or anomaly-block single-request clients."""
    bing, ddg, wiki = await asyncio.gather(
        bing_search(query, max_results),
        duckduckgo_search(query, max_results),
        wikipedia_search(query, min(4, max_results)),
        return_exceptions=True,
    )
    results: list[SearchResult] = []
    for r in (bing, ddg, wiki):
        if isinstance(r, list):
            results.extend(r)
    return results
