"""Clean article extraction via Trafilatura, with BeautifulSoup fallback."""
from __future__ import annotations

import logging

from .html_parser import parse_html

log = logging.getLogger("neuralweb.content")


def extract_clean(html: str, url: str) -> dict:
    """Return {'clean_text', 'markdown', 'raw_text', 'title'} from raw HTML."""
    clean_text = ""
    markdown = ""
    title = ""
    try:
        import trafilatura
        downloaded = trafilatura.extract(
            html, url=url, include_tables=True, include_links=True,
            include_images=True, include_formatting=True, output_format="markdown",
            favor_recall=True)
        if downloaded:
            markdown = downloaded
            clean_text = _md_to_text(downloaded)
            meta = trafilatura.extract_metadata(html=html, url=url)
            if meta and getattr(meta, "title", None):
                title = meta.title
    except Exception as e:  # trafilatura optional/failure tolerant
        log.debug("trafilatura failed on %s: %s", url, e)

    parsed = parse_html(html, url)
    if not title:
        title = parsed.get("title", "")
    if not clean_text:
        clean_text = parsed.get("text", "")
    return {
        "clean_text": clean_text,
        "markdown": markdown,
        "raw_text": parsed.get("text", ""),
        "title": title,
        "parsed": parsed,
    }


def _md_to_text(md: str) -> str:
    """Very light markdown → text so LLM prompts stay token-efficient."""
    lines = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if any(c for c in cells):
                lines.append(" | ".join(c for c in cells if c))
            continue
        if s.startswith("!["):  # images → drop
            continue
        s = _strip_inline_md(s)
        if s:
            lines.append(s)
    return "\n".join(lines)


def _strip_inline_md(s: str) -> str:
    import re
    # links: [text](url) → text (url only if short)
    def repl(m: re.Match) -> str:
        text, href = m.group(1), m.group(2)
        return f"{text} ({href})" if len(href) < 60 and href.startswith("http") else text
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, s)
    s = re.sub(r"[*_#`>]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()
