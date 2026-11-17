"""HTML parsing with BeautifulSoup: structure, metadata, links, tables, JSON-LD, images."""
from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .url_guard import validate_url


def parse_html(html: str, base_url: str, final_url: str = "") -> dict:
    """Extract structured information from raw HTML."""
    soup = BeautifulSoup(html, "lxml")
    out: dict = {"base_url": final_url or base_url}

    # --- title ---
    title_tag = soup.find("title")
    out["title"] = title_tag.get_text(" ", strip=True) if title_tag else ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if not out["title"] and og_title and og_title.get("content"):
        out["title"] = og_title["content"].strip()

    # --- canonical URL ---
    canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
    out["canonical_url"] = ""
    if canonical and canonical.get("href"):
        try:
            out["canonical_url"] = validate_url(urljoin(final_url or base_url, canonical["href"]))
        except Exception:
            out["canonical_url"] = ""

    # --- metadata ---
    meta: dict = {}
    for m in soup.find_all("meta"):
        name = m.get("name") or m.get("property")
        content = m.get("content")
        if name and content:
            meta[name] = content.strip()[:500]
    out["metadata"] = meta

    # --- JSON-LD structured data ---
    jsonld = []
    for s in soup.find_all("script", type="application/ld+json"):
        jsonld.append(s.string or s.get_text())
    out["json_ld"] = [j[:3000] for j in jsonld if j and j.strip()][:5]

    # --- headings ---
    out["headings"] = [
        {"level": int(h.name[1]), "text": h.get_text(" ", strip=True)[:300]}
        for h in soup.find_all(["h1", "h2", "h3", "h4"])
        if h.get_text(strip=True)
    ][:80]

    # --- paragraphs & lists ---
    out["paragraphs"] = [p.get_text(" ", strip=True) for p in soup.find_all("p")
                         if p.get_text(strip=True)][:200]
    out["lists"] = [
        [li.get_text(" ", strip=True) for li in lst.find_all("li")][:30]
        for lst in soup.find_all(["ul", "ol"])[:30]
    ]

    # --- tables ---
    tables = []
    for t in soup.find_all("table")[:20]:
        rows = []
        for tr in t.find_all("tr")[:60]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if any(c for c in cells):
                rows.append(cells)
        if rows:
            tables.append(rows)
    out["tables"] = tables

    # --- images with alt text ---
    out["images"] = [
        {"src": urljoin(out["base_url"], img.get("src", ""))[:500],
         "alt": (img.get("alt") or "").strip()[:200]}
        for img in soup.find_all("img")[:50] if img.get("src")
    ]

    # --- links ---
    internal, external = [], []
    origin_host = urlsplit(out["base_url"]).netloc.lower().removeprefix("www.")
    seen: set = set()
    for a in soup.find_all("a", href=True):
        raw = a["href"].strip()
        if not raw or raw.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        absu = urljoin(out["base_url"], raw)
        if not absu.startswith(("http://", "https://")):
            continue
        host = urlsplit(absu).netloc.lower().removeprefix("www.")
        if not host:
            continue
        key = (host, urlsplit(absu).path)
        if key in seen:
            continue
        seen.add(key)
        if host == origin_host:
            internal.append(absu)
        else:
            external.append(absu)
    out["internal_links"] = internal[:300]
    out["external_links"] = external[:200]
    out["text"] = soup.get_text(" ", strip=True)
    return out
