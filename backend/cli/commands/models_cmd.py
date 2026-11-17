"""providers / models / usage / benchmarks / config commands."""
from __future__ import annotations

import asyncio

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ...app.benchmarks import harness
from ...app.database import repository as repo
from ...app.providers import manager as pm
from ...app.quotas.tracker import quota_tracker
from ...app.computer import terminal as computer_terminal
from ..ui import render

from ..theme import console  # noqa: F811  (themed console)
app = typer.Typer(help="Providers, models, quotas, benchmarks and configuration",
                  no_args_is_help=True)


@app.command("providers")
def providers(
    add: bool = typer.Option(False, "--add", help="Add/update a provider key"),
    test: str = typer.Option("", "--test", help="Test provider connection (provider id)"),
    provider_id: str = typer.Option("", "--id"),
    api_key: str = typer.Option("", "--key", envvar="NW_PROVIDER_KEY"),
    base_url: str = typer.Option("", "--base-url"),
):
    """List, add and test AI providers (keys stored securely, never logged)."""
    if add:
        if not provider_id:
            console.print("[err]--id required (groq, gemini, openai, openrouter, cerebras, together, custom)[/err]")
            raise typer.Exit(1)
        if not api_key:
            import getpass
            api_key = getpass.getpass(f"API key for {provider_id}: ")
        p = pm.update_provider(provider_id, api_key=api_key,
                               base_url=base_url or None)
        console.print(f"[ok]key stored[/ok] for {p['id']} "
                      f"({p['api_key_masked']}, storage: {p['key_source']})")
        return
    if test:
        with console.status("[accent]testing connection (real API call)...[/accent]"):
            res = asyncio.run(pm.test_connection(test, ""))
        if res.get("ok"):
            console.print(f"[ok]✔ {test} works[/ok]  model={res.get('model')} "
                          f"latency={res.get('latency_ms')}ms reply={res.get('reply', '')[:20]!r}")
        else:
            console.print(Panel(f"[err]connection failed:[/err] {res.get('error')}",
                                title="Test result", border_style="red"))
        return
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Provider"); t.add_column("Kind"); t.add_column("Endpoint")
    t.add_column("Key"); t.add_column("Enabled")
    for p in pm.list_providers():
        t.add_row(p["id"], p["kind"], p["base_url"] or "(set with --base-url)",
                  p["api_key_masked"] or "[muted]—[/muted]",
                  "[ok]✔[/ok]" if p["enabled"] else "[muted]off[/muted]")
    console.print(t)
    console.print("[muted]add a key: neural-web providers add --id groq --key …[/muted]")


@app.command("models")
def models(
    add: bool = typer.Option(False, "--add"),
    provider: str = typer.Option("", "--provider", help="Provider id for --add / --import"),
    model_id: str = typer.Option("", "--model-id", help="Model id for --add"),
    import_catalog: bool = typer.Option(False, "--import", help="Import models from provider API"),
    prefix: str = typer.Option("", "--prefix", help="Only import model ids starting with prefix"),
    select: str = typer.Option("", "--select", help="Set default model (provider/model)"),
    task: str = typer.Option("", "--task", help="Assign model to a task (e.g. research, extract)"),
    remove: str = typer.Option("", "--remove"),
    test: str = typer.Option("", "--test", help="Test a configured model (provider/model)"),
    ctx: bool = typer.Option(False, "--with-context"),
    free: bool = typer.Option(False, "--free", help="Show FREE models only "
                              "(verified zero-cost usage; paid & unknown are hidden)"),
):
    """Model manager: add, import, select, assign, test, remove — nothing hardcoded."""
    if free:
        res = pm.list_free_models()
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan",
                  title="FREE models — verified zero-cost API usage (live)")
        t.add_column("Provider"); t.add_column("Model ID"); t.add_column("Context", justify="right")
        t.add_column("Verification")
        for m in sorted(res["free"], key=lambda x: (x["provider"], x["model_id"])):
            t.add_row(m["provider"], m["model_id"],
                      str(m["context_tokens"] or "?"), m["source"],
                      style="green")
        if not res["free"]:
            console.print("[warn]no free models discoverable — connect a key: "
                          "neural-web api add groq[/warn]")
        console.print(t)
        if res["excluded_unknown"]:
            console.print("[muted]hidden (pricing unknown): " +
                          ", ".join(res["excluded_unknown"]) + "[/muted]")
        console.print("[muted]auto-refreshed from provider APIs on every call; "
                      "free CONTEXT ≠ free API usage — only zero-cost usage shown[/muted]")
        return
    if add:
        if not (provider and model_id):
            console.print("[err]--provider and --model-id required[/err]"); raise typer.Exit(1)
        m = pm.add_model(provider, model_id, is_default=not models_has_default())
        console.print(f"[ok]model added[/ok] {m['id']}")
    if import_catalog:
        n = pm.import_catalog_models(provider, [prefix] if prefix else [])
        console.print(f"[ok]imported {n} models from {provider} catalog[/ok]")
    if select:
        pm.set_default_model(select)
        console.print(f"[ok]default model → {select}[/ok]")
    if task and select:
        pm.set_task_model(select, task)
        console.print(f"[ok]{select} assigned to task '{task}'[/ok]")
    if remove:
        pm.remove_model(remove)
        console.print(f"[ok]removed {remove}[/ok]")
    if test:
        with console.status("[accent]testing (real call)...[/accent]"):
            res = asyncio.run(pm.test_connection(test.split("/")[0], test.split("/", 1)[1]))
        if res.get("ok"):
            console.print(f"[ok]✔ {test} → {res.get('reply', '')[:20]!r} "
                          f"({res.get('latency_ms')}ms)[/ok]")
        else:
            console.print(f"[err]✗ {test}: {res.get('error')}[/err]")
    if add or import_catalog or select or remove or test:
        return
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Model (provider/model)"); t.add_column("Provider")
    t.add_column("Default"); t.add_column("Task"); t.add_column("Enabled")
    if ctx:
        t.add_column("Context", justify="right")
    for m in pm.list_models():
        row = [m["id"], m["provider_id"],
               "[purple]★[/purple]" if m["is_default"] else "",
               m["task"] or "—",
               "[ok]✔[/ok]" if m["enabled"] else "[muted]off[/muted]"]
        if ctx:
            row.append(f"{m['context_tokens'] or '?'}")
        t.add_row(*row)
    console.print(t)
    console.print("[muted]add: models add --provider groq --model-id openai/gpt-oss-120b --select[/muted]")


def models_has_default() -> bool:
    return any(m["is_default"] for m in pm.list_models())


@app.command("usage")
def usage(
    model: str = typer.Option("", "--model"),
    provider: str = typer.Option("", "--provider"),
    all: bool = typer.Option(False, "--all", help="Show every configured model"),
    refresh: bool = typer.Option(False, "--refresh", help="Query provider quota APIs"),
    set_limit: int = typer.Option(0, "--set-limit", help="Manually set token limit for --model"),
    period: str = typer.Option("manual", "--period", help="daily | monthly | manual"),
    clear_limit: bool = typer.Option(False, "--clear-limit"),
):
    """Quota dashboard: real per-model usage + limits (never invented)."""
    if refresh:
        results = asyncio.run(quota_tracker.refresh_provider_quota(provider or "openrouter"))
        for r in results:
            console.print(f"  · {r.get('model_ref', r.get('provider'))}: {r.get('note') or r}")
    if set_limit and model:
        quota_tracker.set_manual_limit(model, set_limit, period)
        console.print(f"[ok]manual limit set[/ok] {model}: {set_limit:,} tokens/{period}")
    if clear_limit and model:
        quota_tracker.clear_limit(model)
        console.print(f"[ok]limit cleared[/ok] for {model}")
    if model and not set_limit:
        q = quota_tracker.get_quota(model)
        console.print(Panel(render.quota_bar(q["used_tokens"], q["limit_tokens"]),
                            title=f"{model} — {q['period']}", border_style="cyan"))
        console.print(f"[muted]source: {q['source']}  calls: {q['used_calls']} "
                      f"(ok {q['ok_calls']}, failed {q['failed_calls']})[/muted]")
        return
    overview = quota_tracker.overview(provider or None)
    console.print(render.model_usage_table(overview["models"]))
    total = overview["total"]
    if total.get("aggregated"):
        console.print(f"[purple]combined ({total['period']}):[/purple] "
                      f"{total['used_tokens']:,}/{total['limit_tokens']:,} "
                      f"({total['percent_used']}%)")
    else:
        console.print(f"[muted]{total.get('note', '')}[/muted]")


@app.command("benchmarks")
def benchmarks(
    run: str = typer.Option("", "--run", help="Run benchmarks for provider/model"),
    caps: str = typer.Option("", "--caps", help="Comma-separated capabilities (default all)"),
    show: str = typer.Option("", "--show", help="Show stored benchmark summary"),
):
    """Reproducible model capability tests (real measurements, never fake)."""
    if run:
        cap_list = [c.strip() for c in caps.split(",") if c.strip()] or None
        with console.status("[accent]running real model benchmarks...[/accent]"):
            res = asyncio.run(harness.run_benchmark(run, cap_list))
        if res.get("error"):
            console.print(f"[err]{res['error']}[/err]"); raise typer.Exit(1)
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan",
                  title=f"Measured capabilities — {res['model_ref']}")
        t.add_column("Capability"); t.add_column("Score", justify="right")
        t.add_column("Detail", max_width=60, overflow="fold")
        for cap, r in res["results"].items():
            if r.get("status") == "untested":
                t.add_row(cap, "[muted]—[/muted]", "untested (no benchmark defined)")
            else:
                score = r["score"]
                color = "green" if score >= 8 else ("yellow" if score >= 5 else "red")
                t.add_row(cap, f"[{color}]{score:.1f}/10[/]", r.get("detail", ""))
        console.print(t)
        console.print("[muted]scores are mechanical validators, stored with timestamp; "
                      "source=measured[/muted]")
        return
    ref = show or (pm.resolve_model("auto") or {}).get("id", "")
    if not ref:
        console.print("[err]no model configured[/err]"); raise typer.Exit(1)
    summary = harness.summarize(ref)
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan",
              title=f"Benchmarks — {ref}")
    t.add_column("Capability"); t.add_column("Score", justify="right")
    t.add_column("Source"); t.add_column("Tested")
    for cap, b in summary["capabilities"].items():
        score = b["score"]
        style = "green" if score >= 8 else ("yellow" if score >= 5 else "red")
        t.add_row(cap, f"[{style}]{score:.1f}[/]" if b["source"] == "measured" else f"{score:.0f}",
                  b["source"], b["tested_at"][:16])
    if summary["average_measured"] is not None:
        console.print(f"[purple]average (measured only): {summary['average_measured']}/10[/purple]")
    console.print(t)


def model_switch_ui(query: str, invocation: str = "neural-web model") -> None:
    """Fuzzy model listing/switching, shared by chat mode and the CLI."""
    models = pm.list_models()
    if not models:
        console.print("[err]no models configured[/err] — add one: "
                      "neural-web api add groq  →  neural-web model add groq openai/gpt-oss-120b")
        return
    if not query:
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
        t.add_column("Model"); t.add_column("Provider"); t.add_column("Active")
        for m in models:
            t.add_row(m["id"], m["provider_id"],
                      "[purple]★[/purple]" if m["is_default"] else "")
        console.print(t)
        console.print(f"[muted]switch: {invocation} <name-part>  (e.g. {invocation} 20b)[/muted]")
        return
    q = query.lower()
    matches = [m for m in models if q in m["id"].lower()]
    if len(matches) > 1:
        # prefer an exact name match, then an exact token ("20b" ≠ "120b")
        import re as _re
        ends = [m for m in matches if m["id"].rsplit("/", 1)[-1].lower() == q]
        if not ends:
            ends = [m for m in matches
                    if q in _re.split(r"[^a-z0-9]+", m["id"].rsplit("/", 1)[-1].lower())]
        matches = ends or matches
    if not matches:
        console.print(f"[err]no model matches {query!r}[/err]")
        return
    if len(matches) > 1:
        console.print("[warn]several models match:[/warn]")
        for m in matches:
            console.print(f"  {m['id']}")
        console.print("[muted]be more specific[/muted]")
        return
    ref = matches[0]["id"]
    pm.set_default_model(ref)
    console.print(f"[ok]✔ active model →[/ok] [cyan]{ref}[/cyan]")


def model_add_ui(provider: str, model_id: str, invocation: str = "/model add") -> None:
    """Add a model (with live catalog context enrichment), shared UI."""
    if not provider or not model_id:
        console.print(f"[muted]usage: {invocation} <provider> <model-id>[/muted]  "
                      "(e.g. /model add groq meta-llama/llama-4-scout-17b-16e-instruct)")
        return
    try:
        m = pm.add_model(provider, model_id)
    except Exception as e:
        console.print(f"[err]could not add:[/err] {e}")
        return
    ctx = f", context: {m['context_tokens']}" if m.get("context_tokens") else ""
    console.print(f"[ok]✔ model added[/ok] [cyan]{m['id']}[/cyan]{ctx}")
    console.print(f"[muted]switch to it: {invocation} {model_id.split('/')[-1]}[/muted]")


@app.command("model")
def model_cmd(
    query: list[str] = typer.Argument(None, help="name part → switch · 'add <provider> <model-id>' → add"),
):
    """Simple model switching:  neural-web model 20b
    Add a model:  neural-web model add groq openai/gpt-oss-120b"""
    text = " ".join(query or []).strip()
    if text.lower().startswith("add "):
        parts = text.split()
        model_add_ui(parts[1] if len(parts) > 1 else "",
                     parts[2] if len(parts) > 2 else "", "neural-web model add")
        return
    model_switch_ui(text)


@app.command("config")
def config(
    show: bool = typer.Option(True, "--show"),
    switch: str = typer.Option("", "--switch", help="terminal_enabled | browser_enabled | network_enabled | global_stop"),
    value: bool = typer.Option(True, "--value/--disable"),
    emergency_stop: bool = typer.Option(False, "--emergency-stop"),
    emergency_clear: bool = typer.Option(False, "--emergency-clear"),
):
    """Configuration & emergency controls."""
    if emergency_stop:
        res = computer_terminal.emergency_stop()
        console.print(f"[err]EMERGENCY STOP[/err] — processes killed: {len(res['processes_stopped'])}, "
                      f"tasks signalled: {res['tasks_signalled']}, subsystems disabled")
        return
    if emergency_clear:
        computer_terminal.clear_emergency_stop()
        console.print("[ok]controls re-enabled[/ok]")
        return
    if switch:
        repo.set_setting(switch, "true" if value else "false")
    st = computer_terminal.switch_status()
    t = Table(box=box.SIMPLE, header_style="bold cyan")
    t.add_column("Switch"); t.add_column("State")
    for k, v in st.items():
        t.add_row(k, "[ok]on[/ok]" if v else "[err]off[/err]")
    console.print(t)


def api_add_flow(provider: str, key: str = "", base_url: str = "") -> bool:
    """Paste-an-API-key flow: store → real test → import models → activate.
    Used by `neural-web api add`, chat mode `/api` and the shell."""
    known = [p["id"] for p in pm.list_providers()]
    if provider not in known:
        console.print(f"[err]unknown provider {provider!r} — one of: {', '.join(known)}[/err]")
        return False
    if provider == "custom" and not base_url:
        console.print("[err]custom provider needs a base URL[/err]")
        return False
    if not key:
        import getpass
        key = getpass.getpass(f"API key for {provider} (input hidden): ")
    if not key:
        console.print("[err]no key entered[/err]")
        return False
    p = pm.update_provider(provider, api_key=key, base_url=base_url or None)
    console.print(f"[ok]✔ key stored[/ok] for [cyan]{provider}[/cyan] "
                  f"({p['api_key_masked']}, storage: {p['key_source']})")
    if p["key_source"] == "database":
        console.print("[muted]stored in the app database (plaintext on disk) — "
                      "prefer an OS keyring or an env var[/muted]")
    with console.status("[accent]verifying (real API call)...[/accent]"):
        res = asyncio.run(pm.test_connection(provider, ""))
    if not res.get("ok"):
        console.print(f"[yellow]key saved, but test failed:[/yellow] {res.get('error')}")
        return False
    console.print(f"[ok]✔ {provider} works[/ok]  model={res.get('model')} "
                  f"latency={res.get('latency_ms')}ms")
    had_models = bool([m for m in pm.list_models(provider) if m["enabled"]])
    # import chat models straight from the live provider catalog
    n = pm.import_catalog_models(provider, [])
    if n:
        console.print(f"[ok]imported {n} models from {provider} catalog[/ok]")
    else:
        # some providers (e.g. custom endpoints) expose no catalog — add one inline
        mid = console.input("[accent]model id to add (e.g. glm-5.3): [/]").strip()
        if mid:
            try:
                m = pm.add_model(provider, mid)
                console.print(f"[ok]✔ model added[/ok] [cyan]{m['id']}[/cyan]")
            except Exception as e:
                console.print(f"[err]could not add model:[/err] {e}")
    # a brand-new provider becomes active immediately (paste a key → it runs);
    # re-adding an existing provider never hijacks your current default
    if not had_models:
        models = [m for m in pm.list_models(provider) if m["enabled"]]
        if models:
            pm.set_default_model(models[0]["id"])
            console.print(f"[ok]✔ active model →[/ok] [cyan]{models[0]['id']}[/cyan]")
    console.print("[muted]switch anytime: /model <name>[/muted]")
    return True


@app.command("api")
def api(
    action: str = typer.Argument("", help="add | remove | test | (empty = list)"),
    provider: str = typer.Argument("", help="groq | gemini | openai | openrouter | cerebras | together | custom"),
    key: str = typer.Option("", "--key", envvar="NW_API_KEY", help="Key inline (else secure hidden prompt)"),
    base_url: str = typer.Option("", "--base-url", help="Endpoint (required for custom provider)"),
):
    """Add an API key in one simple command:  neural-web api add groq"""
    action = action.lower().lstrip("-")
    known = [p["id"] for p in pm.list_providers()]

    if action == "add":
        if not provider:
            console.print("[accent]Available providers:[/accent]")
            for i, pid in enumerate(known, 1):
                console.print(f"  [cyan]{i}[/cyan]. {pid}")
            choice = console.input("[accent]provider (number or name, enter = groq): [/accent]").strip() or "1"
            provider = known[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(known) else choice
        if provider == "custom" and not base_url:
            base_url = console.input("[accent]base URL (OpenAI-compatible endpoint): [/accent]").strip()
        api_add_flow(provider, key=key, base_url=base_url)
        return

    if action == "remove":
        if not provider:
            console.print("[err]usage: neural-web api remove groq[/err]"); raise typer.Exit(1)
        pm.remove_provider_key(provider)
        console.print(f"[ok]key removed[/ok] for {provider}")
        return

    if action == "test":
        targets = [provider] if provider else \
            [p["id"] for p in pm.list_providers() if p["api_key_masked"]]
        if not targets:
            console.print("[muted]no keys configured — add one: neural-web api add groq[/muted]")
            return
        for pid in targets:
            with console.status(f"[accent]testing {pid}...[/accent]"):
                res = asyncio.run(pm.test_connection(pid, ""))
            if res.get("ok"):
                console.print(f"  [ok]✔[/ok] [cyan]{pid}[/cyan] — model={res.get('model')} "
                              f"latency={res.get('latency_ms')}ms")
            else:
                console.print(f"  [err]✗[/err] [cyan]{pid}[/cyan] — {res.get('error')}")
        return

    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Provider"); t.add_column("Key"); t.add_column("Storage"); t.add_column("Endpoint")
    for p in pm.list_providers():
        t.add_row(p["id"], p["api_key_masked"] or "[muted]—[/muted]",
                  p.get("key_source") or "—", p["base_url"] or "")
    console.print(t)
    console.print("[muted]add a key:   neural-web api add groq[/muted]")
    console.print("[muted]then pick models:  models --import --provider groq[/muted]")
