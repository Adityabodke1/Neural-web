"""sources / explain-source / history / export / ask commands."""
from __future__ import annotations

import json
import os

import typer
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from ...app.ai.analyzer import analyzer
from ...app.database import repository as repo
from ...app.database.database import db
from ...app.exports.exporters import export_session
from ...app.memory.vector_store import vector_store
from ..ui import render

from ..theme import console  # noqa: F811  (themed console)
app = typer.Typer(help="Sources, history, memory, exports and research chat",
                  no_args_is_help=True)


@app.command("sources")
def sources(
    last: bool = typer.Option(False, "--last", help="Sources of the most recent session"),
    domain: str = typer.Option("", "--domain", help="Filter by domain"),
    session: str = typer.Option("", "--session", "-s"),
    record: int = typer.Option(0, "--record", help="Show all sources behind a record"),
):
    """Source transparency: every page and the sources behind extracted fields."""
    if record:
        with db() as conn:
            row = conn.execute(
                "SELECT r.*, s.prompt AS session_prompt FROM records r "
                "LEFT JOIN sessions s ON s.id=r.session_id WHERE r.id=?", (record,)).fetchone()
        if not row:
            console.print("[err]record not found[/err]"); raise typer.Exit(1)
        d = dict(row); d["data"] = json.loads(d.pop("data_json"))
        console.print(Panel(render.explain_record(d),
                            title=f"Record #{record} — field-level sources",
                            border_style="magenta"))
        # page snapshots backing this record
        page = repo.get_page(d["session_id"], d["url"])
        if page:
            console.print(Panel(
                f"[source]{page['final_url'] or page['url']}[/source]\n"
                f"[muted]retrieved:[/] {page['scraped_at']}  [muted]method:[/] {page.get('domain') and 'stored'}  "
                f"[muted]http:[/] {json.loads(page['metadata_json'] or '{}').get('http_status', '?')}  "
                f"[muted]hash:[/] {page['content_hash'][:16]}…\n"
                f"[muted]content preview:[/] {(page['clean_text'] or '')[:400]}…",
                title="Backing page snapshot", border_style="blue"))
        return
    if last and not session:
        sessions = repo.list_sessions(1)
        if not sessions:
            console.print("[err]no sessions yet[/err]"); raise typer.Exit(1)
        session = sessions[0]["id"]
    with db() as conn:
        sql = ("SELECT p.*, s.prompt AS sp FROM pages p LEFT JOIN sessions s ON s.id=p.session_id WHERE 1=1")
        args: list = []
        if session:
            sql += " AND p.session_id=?"; args.append(session)
        if domain:
            sql += " AND p.domain LIKE ?"; args.append(f"%{domain}%")
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY p.id DESC LIMIT 100", args).fetchall()]
    if not rows:
        console.print("[warn]no stored sources match[/warn]"); raise typer.Exit(0)
    console.print(render.sources_table(rows))
    console.print(f"[muted]{len(rows)} sources — use --record N for field-level detail[/muted]")


@app.command("explain-source")
def explain_source(
    record: int = typer.Option(..., "--record", help="Record id"),
    field: str = typer.Option("", "--field", help="Field to explain (default: all)"),
):
    """Show exactly where each field of a record came from."""
    with db() as conn:
        row = conn.execute("SELECT * FROM records WHERE id=?", (record,)).fetchone()
    if not row:
        console.print("[err]record not found[/err]"); raise typer.Exit(1)
    d = dict(row); d["data"] = json.loads(d.pop("data_json"))
    console.print(Panel(render.explain_record(d, field or None),
                        title=f"Explain source — record #{record}", border_style="cyan"))


@app.command("history")
def history(limit: int = typer.Option(20, "--limit")):
    """Research session history."""
    sessions = repo.list_sessions(limit)
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Session"); t.add_column("Prompt", max_width=46, overflow="fold")
    t.add_column("Depth"); t.add_column("Status")
    t.add_column("Pages", justify="right"); t.add_column("Records", justify="right")
    t.add_column("When", style="muted")
    for s in sessions:
        t.add_row(s["id"][:8], s["prompt"][:60], s["depth"],
                  f"[ok]{s['status']}[/ok]" if s["status"] == "complete"
                  else (f"[err]{s['status']}[/err]" if s["status"] == "failed"
                        else s["status"]),
                  str(s["pages_scraped"]), str(s["records_found"]),
                  s["created_at"][:16])
    console.print(t)


@app.command("search-memory")
def search_memory(q: str = typer.Argument(...), top_k: int = typer.Option(8, "--top")):
    """Semantic search over all previous research (local vector memory)."""
    results = vector_store.search(q, top_k=top_k)
    if not results:
        console.print("[warn]memory is empty — run research first[/warn]"); raise typer.Exit(0)
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Score", justify="right"); t.add_column("Kind", style="muted")
    t.add_column("Session", style="muted"); t.add_column("Text", max_width=70, overflow="fold")
    for r in results:
        t.add_row(f"{r['score']:.3f}", r["kind"], r["session_id"][:8], r["text"][:120])
    console.print(t)


@app.command("export")
def export(
    fmt: str = typer.Option("csv", "--format", "-f", help="json | csv | xlsx"),
    session: str = typer.Option("", "--session", "-s", help="Session id (default: latest)"),
):
    """Export a research session to JSON / CSV / Excel (with source columns)."""
    if not session:
        sessions = repo.list_sessions(1)
        if not sessions:
            console.print("[err]nothing to export[/err]"); raise typer.Exit(1)
        session = sessions[0]["id"]
    try:
        out = export_session(session, fmt)
    except ValueError as e:
        console.print(f"[err]{e}[/err]"); raise typer.Exit(1)
    console.print(f"[ok]exported[/ok] {out['records']} records → [source]{out['path']}[/source]")


@app.command("ask")
def ask(
    question: list[str] = typer.Argument(None),
    session: str = typer.Option("", "--session", "-s"),
    model: str = typer.Option("auto", "--model", "-m"),
):
    """Chat with your research memory: facts cited, analysis separated, gaps stated."""
    if not question:
        question = [typer.prompt("Ask about your research")]
    q = " ".join(question)
    if not session:
        sessions = repo.list_sessions(3)
        session = sessions[0]["id"] if sessions else ""
    records = repo.list_records(session) if session else []
    context_pages = []
    if session:
        for p in repo.list_pages(session, include_text=True)[:12]:
            context_pages.append({"url": p["url"], "title": p["title"],
                                  "clean_text": p.get("clean_text", "")})
    with console.status("[accent]thinking...[/accent]"):
        answer = _ask(q, records, context_pages, model)
    console.print(Panel(Markdown(answer), title="NEURAL WEB — answer (facts cited, analysis labelled)",
                        border_style="magenta"))


def _ask(q: str, records: list, pages: list, model: str) -> str:
    import asyncio
    from ...app.ai.provider import records_block, text_block, ai_provider
    recs = records_block(records, 40)
    pgs = "\n\n".join(f"### {p.get('title', '')} ({p.get('url', '')})\n"
                      f"{text_block(p.get('clean_text', ''), 4000)}" for p in pages)
    async def go():
        return await ai_provider.chat(
            [{"role": "system", "content":
                "You are the NEURAL WEB research assistant about the user's collected research.\n"
                "- Facts from sources: cite the source URL inline.\n"
                "- Own analysis: prefix 'Analysis:' and cite nothing.\n"
                "- Missing information: state what was not found.\n"
                "- Never invent citations. Use Markdown."},
             {"role": "user", "content": f"Question: {q}\n\nExtracted records:\n{recs}\n\nPages:\n{pgs}"}],
            request_type="ask", model_spec=model, max_tokens=2000)
    return asyncio.run(go())


def memory(
    clear: bool = typer.Option(False, "--clear", help="Erase everything she remembers"),
    forget: str = typer.Option("", "--forget", help="Remove facts containing this word"),
):
    """What NEURAL WEB remembers about you (memory.txt)."""
    from ...app.voice import companion
    if clear:
        companion.clear()
        console.print(f"[ok]memory cleared — {companion.MEMORY_FILE}[/ok]")
        return
    if forget:
        text = companion.load()
        lines = [l for l in text.splitlines()
                 if forget.lower() not in l.lower()]
        companion.MEMORY_FILE.write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
        console.print(f"[ok]forgot everything containing '{forget}'[/ok]")
        return
    text = companion.load()
    console.print(Panel(
        text or "(nothing yet — talk to her and she'll remember)",
        title=f"🧠 {companion.MEMORY_FILE}", border_style="cyan"))


def conversations(limit: int = typer.Option(20, "--last", help="How many turns to show"),
                  path: bool = typer.Option(False, "--path", help="Just print the file path")):
    """Saved conversations — every chat is stored in one file."""
    from ...app import conversations as conv
    st = conv.stats()
    if path:
        console.print(st["path"])
        return
    turns = conv.recent_turns(limit)
    if not turns:
        console.print(f"[muted]no conversations saved yet — they will go to "
                      f"{st['path']}[/muted]")
        return
    t = Table(box=box.SIMPLE, header_style="bold cyan")
    t.add_column("when", style="muted", width=24)
    t.add_column("who", width=12)
    t.add_column("message", overflow="fold")
    for r in turns:
        role = "you" if r["role"] == "user" else "neural-web"
        color = "cyan" if r["role"] == "user" else "magenta"
        when = conv.format_when(r.get("ts", "")) or str(r.get("ts", ""))
        t.add_row(when, f"[{color}]{role}[/{color}]",
                  r["content"][:400].replace("\n", " "))
    console.print(t)
    console.print(f"[muted]saved file: {st['path']} ({st['turns']} turns). "
                  f"The bot re-reads recent turns on startup and reacts to them.[/muted]")
