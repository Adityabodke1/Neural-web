"""Rich UI helpers: live event rendering, tables, bars, panels."""
from __future__ import annotations

import json

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from ..theme import THEME  # noqa: F401


def quota_bar(used: int | None, limit: int | None, width: int = 28) -> Text:
    if not limit:
        return Text("Quota unavailable — set a limit: neural-web usage set-limit", style="warn")
    pct = min(1.0, (used or 0) / limit)
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    color = "green" if pct < 0.6 else ("yellow" if pct < 0.85 else "red")
    return Text.assemble((bar, color), (f" {pct * 100:.0f}%", "bold"),
                         (f"  {used:,}/{limit:,}", "muted"))


def records_table(records: list[dict], max_rows: int = 30) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", show_lines=False)
    t.add_column("#", style="muted", width=5)
    t.add_column("Entity / Finding", style="value", max_width=34, overflow="fold")
    t.add_column("Fields (value → verification)", max_width=52, overflow="fold")
    t.add_column("Source", style="source", max_width=40, overflow="fold")
    t.add_column("Conf.", justify="right")
    for i, r in enumerate(records[:max_rows], 1):
        data = r["data"]
        fields = data.get("fields", {})
        entity = data.get("entity_name") or next(
            (str(f["value"]) for f in fields.values() if f.get("value")), "—")
        fparts = []
        for k, f in list(fields.items())[:6]:
            v = f.get("value")
            if v is None:
                fparts.append(f"[muted]{k}: null[/muted]")
            else:
                vs = str(v)
                if len(vs) > 40:
                    vs = vs[:37] + "…"
                st = f.get("verification", "unverified")
                color = {"verified": "verified", "partial": "partial",
                         "contradicted": "contradicted"}.get(st, "unverified")
                fparts.append(f"{k}: {vs} [{color}]{st}[/{color}]")
        t.add_row(str(i), entity, "\n".join(fparts) or "—",
                  r.get("url", "")[:60], f"{r.get('confidence', 0):.2f}")
    if len(records) > max_rows:
        t.add_row("…", f"+{len(records) - max_rows} more records", "", "", "")
    return t


def sources_table(sources: list[dict]) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Title", style="value", max_width=44, overflow="fold")
    t.add_column("URL", style="source", max_width=56, overflow="fold")
    t.add_column("Method", style="muted")
    t.add_column("Words", justify="right")
    t.add_column("Status")
    for s in sources[:50]:
        t.add_row(s.get("title") or "—", s.get("final_url") or s.get("url", ""),
                  s.get("method") or "local", str(s.get("word_count", 0)),
                  "[ok]" + s["status"] + "[/ok]" if s.get("status") == "ok"
                  else "[err]" + str(s.get("status")) + "[/err]")
    return t


def explain_record(record: dict, field: str | None = None) -> Group:
    data = record["data"]
    fields = data.get("fields", {})
    if field and field in fields:
        f = fields[field]
        lines = [
            Text.assemble(("FIELD: ", "label"), (field, "value")),
            Text.assemble(("VALUE: ", "label"), (str(f.get("value")), "value")),
            Text.assemble(("SOURCE URL: ", "label"), (f.get("source_url") or "—", "source")),
            Text.assemble(("EVIDENCE: ", "label"), (f.get("evidence") or "—", "value")),
            Text.assemble(("VERIFICATION: ", "label"),
                          (f.get("verification", "unverified"), "verified")),
            Text.assemble(("CONFIDENCE: ", "label"), (f"{f.get('confidence', 0):.2f}", "value")),
            Text.assemble(("CROSS-CHECK SOURCE: ", "label"),
                          (f.get("cross_check_source") or "—", "source")),
        ]
        return Group(*lines)
    lines = [Text.assemble(("ENTITY: ", "label"),
                           (data.get("entity_name") or "—", "value"))]
    for k, f in fields.items():
        lines.append(Text.assemble((f"  {k}: ", "label"), (str(f.get("value")), "value"),
                                   ("  ← ", "muted"), (f.get("source_url") or "—", "source"),
                                   (f"  [{f.get('verification', 'unverified')}]", "muted")))
    lines.append(Text.assemble(("RECORD VERIFICATION: ", "label"),
                               (record.get("verification", ""), "verified")))
    lines.append(Text.assemble(("ALL SOURCES: ", "label"),
                               (", ".join(data.get("sources", [])), "source")))
    return Group(*lines)


def model_usage_table(models: list[dict]) -> Table:
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan", title="MODEL TOKEN QUOTAS")
    t.add_column("Model", style="value")
    t.add_column("Provider", style="muted")
    t.add_column("Usage")
    t.add_column("Period", style="muted")
    t.add_column("Used", justify="right")
    t.add_column("Remaining", justify="right")
    t.add_column("Source", style="muted")
    for m in models:
        q = m["quota"]
        if q["limit_tokens"]:
            used, rem = f"{q['used_tokens']:,}", f"{q['remaining_tokens']:,}"
            period, src = q["period"], ("manual" if "manual" in q["source"] else q["source"])
        else:
            used, rem, period, src = f"{q['used_tokens']:,} (lifetime)", "—", "unlimited?", "usage-log only"
        bar = quota_bar(q["used_tokens"] if q["limit_tokens"] else 0,
                        q["limit_tokens"] or 0) if q["limit_tokens"] else Text("n/a", style="muted")
        t.add_row(m["model_ref"] + (" *" if m["is_default"] else ""), m["provider"],
                  bar, period, used, rem, src)
    t.caption = "* default model — '* limits are manually configured unless marked provider-reported"
    return t


_TOOL_VERB = {
    "write_file": "Writing", "create_file": "Writing", "edit_file": "Editing",
    "read_file": "Reading", "list_directory": "Listing",
    "create_directory": "Creating folder", "delete_path": "Deleting",
    "inspect_project": "Inspecting", "git_diff": "Checking git changes",
    "git_checkpoint": "Saving checkpoint", "execute_command": "Running",
    "check_installed_tools": "Checking installed tools",
    "request_elevation": "Asking for permission", "finish_task": "Wrapping up",
    "install_dependency": "Installing", "build_project": "Building",
    "run_tests": "Testing", "web_search": "Searching", "scrape_page": "Reading",
    "update_plan": "Planning",
    "extract_fields": "Extracting", "store_record": "Saving finding",
    "save_finding": "Saving finding", "verify_claim": "Verifying",
    "verify_record": "Verifying", "summarize_page": "Summarizing",
    "run_report": "Writing the report", "finish_research": "Wrapping up",
}


def _tool_target(args: dict) -> str:
    """Short human target for a tool call — never the full JSON args."""
    for k in ("path", "command", "query", "url", "name", "field", "task"):
        v = args.get(k)
        if v:
            return str(v).replace("\n", " ")[:60]
    return ""


def event_line(evt: dict) -> Text:
    type_ = evt.get("type", "")
    p = evt.get("payload", {})
    if type_ == "note":
        # the agent talking in plain language (Claude-Code style)
        text = " ".join((p.get("text") or "").split())
        return Text(text[:400]) if text else Text("")
    if type_ == "tool":
        tool = p.get("tool", "")
        verb = _TOOL_VERB.get(tool, tool.replace("_", " "))
        target = _tool_target(p.get("args", {}))
        line = f"{verb} {target}".strip()
        return Text.assemble(("⏺ ", "accent"), (line[:110], "bold cyan"))
    if type_ == "tool_result":
        if p.get("ok") and p.get("tool") == "execute_command":
            return Text("")  # the command event already shows the exit code
        if p.get("ok"):
            return Text("  ⎿ ✓", style="muted")
        return Text.assemble(("  ⎿ ", "muted"), ("✗ ", "err"),
                             ((p.get("snippet") or "")[:140], "muted"))
    if type_ == "step":
        # "executing <tool>" steps are redundant with the tool line — skip them
        if p.get("step") == "executing":
            return Text("")
        return Text.assemble(("⏺ ", "accent"),
                             (str(p.get("action", p.get("step", "")))[:90], "bold"))
    if type_ == "search":
        return Text.assemble(("⏺ Search(", "accent"), (p.get("query", ""), "value"),
                             (")", "accent"),
                             (f"  ⎿ {p.get('new_results', 0)} results", "muted"))
    if type_ == "page_scraped":
        return Text.assemble(("  ⎿ fetched ", "muted"), (p.get("url", ""), "source"),
                             (f"  {p.get('words', 0)} words", "muted"))
    if type_ == "page_failed":
        return Text.assemble(("  ⎿ ", "muted"), ("✗ ", "err"), (p.get("url", ""), "err"),
                             (f"  {p.get('error', '')[:80]}", "muted"))
    if type_ == "page_duplicate":
        return Text("")  # stay quiet, Claude-Code style
    if type_ == "record":
        return Text.assemble(("  ⎿ stored ", "muted"),
                             (str(p.get("entity") or p.get("url", ""))[:60], "value"),
                             (f"  #{p.get('record_id')}", "muted"))
    if type_ == "verification":
        return Text.assemble(("  ⎿ verify ", "muted"),
                             (f"record #{p.get('record_id')} → {p.get('status')}", "value"))
    if type_ == "plan":
        return Text.assemble(("⏺ Plan: ", "accent"),
                             (str(p.get("plan", {}).get("objective", ""))[:90], "value"))
    if type_ == "plan_update":
        # live todo list (Claude-Code style): ☐ pending · ◐ in progress · ☑ done
        icons = {"completed": ("☑ ", "ok"), "in_progress": ("◐ ", "warn"),
                 "pending": ("☐ ", "muted")}
        steps = p.get("steps") or []
        if not steps:
            return Text("")
        lines = []
        for s in steps:
            icon, style = icons.get(str(s.get("status", "pending")), ("☐ ", "muted"))
            label = str(s.get("step", ""))[:100]
            dim = "muted" if s.get("status") == "completed" else "value"
            lines.append(Text.assemble(("  " + icon, style), (label, dim)))
        return Text("\n").join(lines)
    if type_ == "summary":
        return Text.assemble(("  ⎿ ", "muted"), (p.get("summary", "")[:120], "muted"))
    if type_ == "error":
        return Text.assemble(("⎿ ✗ ", "err"), (p.get("message", "")[:180], "err"))
    if type_ == "warning":
        return Text.assemble(("⎿ ⚠ ", "warn"), (p.get("message", "")[:180], "warn"))
    if type_ == "command":
        return Text.assemble(("  ⎿ $ ", "muted"), (p.get("command", ""), "value"),
                             (f"  exit={p.get('exit_code')}", "muted"))
    if type_ == "done":
        status = p.get("status", "")
        return Text.assemble(("⏺ " if status == "complete" else "⎿ ✗ ", "ok" if status == "complete" else "err"),
                             (status or "done", "bold" if status == "complete" else "err"))
    return Text("")


def markdown_panel(md: str, title: str = "Report") -> Panel:
    return Panel(Markdown(md or "*(empty)*"), title=title, border_style="cyan",
                 box=box.HEAVY)


def live_renderer(console: Console) -> "Live":
    return Live(console=console, refresh_per_second=8, transient=False)
