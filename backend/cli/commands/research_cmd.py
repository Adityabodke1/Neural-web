"""research / extract / crawl / scrape / compare / monitor commands."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlsplit

import typer
from rich import box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ...app.agent import research_agent
from ...app.agent.monitor import check_page_for_changes
from ...app.agent.task_manager import registry
from ...app.ai.extractor import extractor
from ...app.ai.analyzer import analyzer
from ...app.database import repository as repo
from ...app.database.models import ExtractedRecord
from ...app.providers import manager as pm
from ...app.scraping.engine import scrape_engine
from ..banner import show_header
from ..ui import render

from ..theme import console  # noqa: F811  (themed console)
app = typer.Typer(help="Research, scraping and extraction commands", no_args_is_help=True)


def _validate_prompt_urls(text: str) -> str:
    return text.strip()


async def _stream_events(session_id: str) -> None:
    """Render live task events (same bus as the SSE dashboard)."""
    tm = await registry.get(session_id)
    seen = 0
    while True:
        events = list(tm.events)
        while seen < len(events):
            evt = events[seen]
            seen += 1
            line = render.event_line(evt)
            if line.plain.strip():
                console.print(line)
            if evt.get("type") == "done":
                return
        await asyncio.sleep(0.35)


def research(
    prompt: list[str] = typer.Argument(None, help="Research request in natural language"),
    depth: str = typer.Option("standard", "--depth", "-d", help="quick | standard | deep"),
    max_pages: int = typer.Option(0, "--max-pages", help="Page budget (0 = depth default)"),
    model: str = typer.Option("auto", "--model", "-m", help="provider/model or 'auto'"),
    fields: str = typer.Option("", "--fields", "-f", help="Comma-separated target fields"),
    domains: str = typer.Option("", "--domains", help="Restrict to these domains (csv)"),
    time_limit: int = typer.Option(0, "--time-limit", help="Minutes (0 = default by depth)"),
):
    """Autonomous AI research: plan → search → scrape → extract → verify → report."""
    if not prompt:
        prompt = [typer.prompt("Enter your research request")]
    text = " ".join(prompt).strip()
    if len(text) < 3:
        console.print("[err]Prompt too short.[/err]")
        raise typer.Exit(1)
    m = pm.resolve_model(model) if model != "auto" else pm.resolve_model("auto")
    show_banner(console)
    if not m:
        console.print("[err]No AI model configured. Run:[/] neural-web models add")
        raise typer.Exit(1)

    params = {"max_pages": max_pages, "time_limit_minutes": time_limit,
              "target_fields": [f.strip() for f in fields.split(",") if f.strip()],
              "include_domains": [d.strip() for d in domains.split(",") if d.strip()],
              "model": model}
    session_id = repo.create_session(text, depth, params)

    async def run():
        await research_agent.start_research(session_id)
        await asyncio.wait_for(_stream_events(session_id), timeout=60 * 40)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        tm = asyncio.run(registry.get(session_id, create=False))
        if tm:
            tm.request_stop()
        console.print("[warn]Stop requested — saving partial progress...[/warn]")

    s = repo.get_session(session_id)
    records = repo.list_records(session_id)
    stats = repo.urls_by_status(session_id)
    verified = sum(1 for r in records if r["verification"] in ("verified", "partial"))
    usage = repo.usage_summary()
    console.print()
    console.print(f"[ok][RESULTS][/ok] records: {len(records)}  verified/partial: {verified}  "
                  f"pages scraped: {s.get('pages_scraped')}")
    if records:
        console.print(render.records_table(records))
    if s.get("error"):
        console.print(f"[err]Error:[/err] {s['error']}")
    per_model = usage["per_model"]
    for mm, u in per_model.items():
        console.print(f"[accent][TOKENS][/accent] {mm}: {u['tokens']:,} tokens "
                      f"({u['calls']} calls, {u['failures']} failures)")
    console.print(f"[muted]session: {session_id}[/muted]")
    console.print(f"[muted]export:  neural-web export --session {session_id} --format csv[/muted]")
    if s.get("report_md"):
        console.print(render.markdown_panel(s["report_md"], "Research Report"))


def extract(
    url: str = typer.Argument(..., help="Page URL"),
    fields: str = typer.Argument(..., help="Comma-separated fields to extract"),
    model: str = typer.Option("auto", "--model", "-m"),
    save: bool = typer.Option(True, "--save/--no-save", help="Save to local memory"),
):
    """Extract specific fields from one URL with per-field provenance."""
    target = [f.strip() for f in fields.split(",") if f.strip()]
    async def run():
        page = await scrape_engine.scrape(url, session_id="extract")
        if page.status != "ok":
            console.print(f"[err]scrape failed ({page.status}): {page.error}[/err]")
            raise typer.Exit(1)
        rec = await extractor.extract_fields(
            f"Extract: {fields}", target, page.clean_text or page.raw_text,
            page.url, page.title)
        return page, rec
    page, rec = asyncio.run(run())
    record_dict = {"data": rec.data, "url": rec.url, "confidence": rec.confidence,
                   "verification": rec.verification}
    console.print(render.explain_record(record_dict))
    if save:
        sid = repo.create_session(f"[extract] {fields} from {url}", "quick",
                                  {"target_fields": target})
        rid = repo.save_record(sid, rec, page.content_hash)
        repo.save_page(sid, {"url": page.url, "final_url": page.final_url,
                             "status": "ok", "title": page.title,
                             "raw_text": page.raw_text[:30_000],
                             "clean_text": page.clean_text[:50_000],
                             "metadata": page.metadata, "content_hash": page.content_hash,
                             "word_count": page.word_count, "method": "extract"})
        console.print(f"[muted]saved as record #{rid} in session {sid}[/muted]")


def scrape(
    url: str = typer.Argument(...),
    js: bool = typer.Option(False, "--js", help="Render with headless browser"),
    save: bool = typer.Option(False, "--save", help="Store in local memory"),
):
    """Scrape a single URL and show clean content + metadata."""
    async def run():
        return await scrape_engine.scrape(url, use_js=js, session_id="cli")
    page = asyncio.run(run())
    if page.status != "ok":
        console.print(f"[err]scrape failed ({page.status}): {page.error}[/err]")
        raise typer.Exit(1)
    console.print(Panel(Text.assemble(
        ("title: ", "label"), (page.title or "—", "value"), "\n",
        ("final_url: ", "label"), (page.final_url, "source"), "\n",
        ("hash: ", "label"), (page.content_hash[:16] + "…", "muted"), "  ",
        ("words: ", "label"), (str(page.word_count), "value")),
        title=f"[ok]scraped ({'playwright' if js else 'httpx'})[/ok]", border_style="cyan"))
    console.print(Panel(page.clean_text[:3000] or "(no clean text)",
                        title="Clean content", border_style="blue"))
    if page.metadata.get("headings"):
        t = Table(box=box.SIMPLE, header_style="bold cyan")
        t.add_column("Level"); t.add_column("Heading")
        for h in page.metadata["headings"][:15]:
            t.add_row(str(h["level"]), h["text"])
        console.print(t)
    if save:
        sid = repo.create_session(f"[scrape] {url}", "quick", {})
        repo.save_page(sid, {"url": page.url, "final_url": page.final_url, "status": "ok",
                             "title": page.title, "raw_text": page.raw_text[:30_000],
                             "clean_text": page.clean_text[:50_000], "metadata": page.metadata,
                             "content_hash": page.content_hash, "word_count": page.word_count,
                             "method": "cli"})
        console.print(f"[muted]saved in session {sid}[/muted]")


def crawl(
    url: str = typer.Argument(...),
    depth: int = typer.Option(1, "--depth", min=1, max=3),
    max_pages: int = typer.Option(6, "--max-pages", min=1, max=40),
    save: bool = typer.Option(True, "--save/--no-save"),
):
    """Crawl a domain with dedup + robots.txt compliance + live counters."""
    console.print(f"[accent][CRAWLER][/accent] starting at {url} (depth={depth}, max={max_pages})")
    sid = repo.create_session(f"[crawl] {url}", "quick", {"max_pages": max_pages})

    async def run():
        from urllib.parse import urlsplit
        start_host = urlsplit(url).netloc.lower().removeprefix("www.")
        seen, frontier, results = set(), [(url, 0)], []
        while frontier and len(seen) < max_pages:
            u, d = frontier.pop(0)
            if u in seen or d > depth:
                continue
            seen.add(u)
            page = await scrape_engine.scrape(u, session_id=sid)
            if page.status == "ok":
                console.print(f"  [ok][+][/ok] {page.url}  [muted]{page.word_count} words[/muted]")
                if save:
                    repo.save_page(sid, {"url": page.url, "final_url": page.final_url,
                                         "status": "ok", "title": page.title,
                                         "raw_text": page.raw_text[:30_000],
                                         "clean_text": page.clean_text[:50_000],
                                         "metadata": page.metadata,
                                         "content_hash": page.content_hash,
                                         "word_count": page.word_count,
                                         "method": page.metadata.get("provider") or "crawl"})
                for link in page.links[:30]:
                    try:
                        from ...app.scraping.url_guard import validate_url
                        link = validate_url(link)
                    except Exception:
                        continue
                    if urlsplit(link).netloc.lower().removeprefix("www.") == start_host \
                            and link not in seen:
                        frontier.append((link, d + 1))
            else:
                console.print(f"  [err][✗][/err] {u}  [muted]{page.error[:80]}[/muted]")
        return len(seen)

    n = asyncio.run(run())
    repo.update_session(sid, status="complete", pages_scraped=n)
    console.print(f"[ok]crawled {n} pages[/ok]  [muted]session: {sid}[/muted]")


def compare(
    urls: list[str] = typer.Argument(..., help="2+ URLs to compare"),
    aspect: str = typer.Option("pricing and key product features", "--aspect", "-a"),
    model: str = typer.Option("auto", "--model", "-m"),
):
    """Compare multiple websites with source-cited output."""
    if len(urls) < 2:
        console.print("[err]provide at least 2 URLs[/err]"); raise typer.Exit(1)
    async def run():
        pages = []
        for u in urls:
            page = await scrape_engine.scrape(u, session_id="compare")
            if page.status == "ok":
                pages.append({"url": page.url, "text": page.clean_text or page.raw_text})
        return pages
    pages = asyncio.run(run())
    if len(pages) < 2:
        console.print("[err]could not scrape enough pages to compare[/err]"); raise typer.Exit(1)
    with console.status("[accent]comparing...[/accent]"):
        out = asyncio.run(analyzer.compare_sites(pages, aspect))
    console.print(render.markdown_panel(out, "Comparison"))


def monitor(
    url: str = typer.Argument(...),
    watch: bool = typer.Option(False, "--watch", help="Repeat every 10 minutes until Ctrl+C"),
):
    """Change detection: snapshot a page and diff against the previous snapshot."""
    async def run():
        return await check_page_for_changes(url, "monitor")
    res = asyncio.run(run())
    if res.get("status") not in (None, "ok"):
        console.print(f"[err]{res.get('error', res.get('status'))}[/err]"); raise typer.Exit(1)
    if res.get("first_check"):
        console.print("[warn]first snapshot stored — run again later to detect changes[/warn]")
        raise typer.Exit(0)
    if not res.get("change_detected"):
        console.print(f"[ok]no changes[/ok] ({res.get('note', '')})"); raise typer.Exit(0)
    console.print(f"[warn]CHANGES DETECTED[/warn] (similarity {res.get('similarity')})")
    ch = res.get("changes", {})
    for title, key, style in (("Added lines", "added", "ok"),
                              ("Removed lines", "removed", "err"),
                              ("Changed", "changed", "warn")):
        items = ch.get(key, [])
        if items:
            console.print(f"\n[bold]{title}[/bold]")
            for it in items[:15]:
                if isinstance(it, dict):
                    console.print(f"  [{style}]●[/]{it.get('old', '')} → {it.get('new', '')}")
                else:
                    console.print(f"  [{style}]●[/]{it}")
    if ch.get("prices_removed") or ch.get("prices_added"):
        console.print(f"\n[purple]price changes:[/purple] removed {ch['prices_removed']} "
                      f"added {ch['prices_added']}")
