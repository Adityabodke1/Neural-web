"""permissions / coding / clear-cache commands."""
from __future__ import annotations

import asyncio
import json

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ...app.computer import coding_agent, filesystem, permissions, workspace
from ...app.agent.task_manager import registry
from ..banner import show_banner
from ..ui import render

from ..theme import console  # noqa: F811  (themed console)
app = typer.Typer(help="Folder permissions and the autonomous coding agent",
                  no_args_is_help=True)


@app.command("permissions")
def permissions_cmd(
    add_folder: str = typer.Option("", "--add-folder", help="Authorize a folder"),
    revoke: str = typer.Option("", "--revoke", help="Revoke a folder"),
    audit: int = typer.Option(0, "--audit", help="Show last N audit entries"),
    sandbox: bool = typer.Option(False, "--sandbox", help="Show sandbox status"),
):
    """Folder-level access control: authorize once, then work freely inside."""
    if add_folder:
        try:
            f = permissions.authorize_folder(add_folder)
            console.print(f"[ok]authorized[/ok] {f['path']}  [muted]os={f['os']}[/muted]")
        except permissions.PermissionDenied as e:
            console.print(f"[err]{e}[/err]"); raise typer.Exit(1)
        return
    if revoke:
        if permissions.revoke_folder(revoke):
            console.print(f"[ok]revoked[/ok] {revoke}")
        else:
            console.print("[warn]folder not found[/warn]")
        return
    if sandbox:
        from ...app.computer.sandbox import sandbox_status
        s = sandbox_status()
        console.print(Panel(
            f"os: {s['os']}  backend: {s['backend']}\nmode: "
            f"[{'ok' if s['os_isolation_available'] else 'warn'}]{s['mode']}[/]\n{s['note']}",
            title="Sandbox", border_style="cyan"))
        return
    folders = permissions.list_folders()
    t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    t.add_column("Folder"); t.add_column("OS"); t.add_column("Active")
    t.add_column("Files accessed", justify="right"); t.add_column("Last activity")
    for f in folders:
        t.add_row(f["path"], f.get("os", ""), "[ok]✔[/ok]" if f.get("active") else "[muted]revoked[/muted]",
                  str(f.get("files_accessed", 0)), f.get("last_activity") or "—")
    console.print(t)
    pending = permissions.list_approvals()
    if pending:
        console.print("[warn]pending approvals:[/warn]")
        for a in pending:
            console.print(f"  #{a['id']} [{a['kind']}] {a['description'][:90]}")
        console.print("[muted]approve: neural-web coding approve <id>[/muted]")


@app.command("approve")
def approve(approval_id: int = typer.Argument(...), deny: bool = typer.Option(False, "--deny")):
    """Approve or deny a pending level-3 operation."""
    ok = permissions.decide_approval(approval_id, not deny)
    console.print(f"[{'ok' if ok else 'warn'}]{'decided' if ok else 'not found or already decided'}[/]")


@app.command("coding")
def coding(
    task: list[str] = typer.Argument(None, help="Describe the coding task"),
    workspace_path: str = typer.Option("", "--workspace", "-w", help="Authorized workspace folder"),
    model: str = typer.Option("auto", "--model", "-m"),
    max_steps: int = typer.Option(80, "--max-steps"),
    list_tasks: bool = typer.Option(False, "--list"),
):
    """Autonomous coding agent: builds real projects inside authorized folders."""
    if list_tasks:
        tasks = coding_agent.list_coding_tasks()
        t = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
        t.add_column("#"); t.add_column("Workspace"); t.add_column("Status")
        t.add_column("Files", justify="right"); t.add_column("Cmds", justify="right")
        t.add_column("Task", max_width=50, overflow="fold")
        for tk in tasks:
            t.add_row(str(tk["id"]), tk["workspace"], tk["status"],
                      str(tk["files_touched"]), str(tk["commands_run"]),
                      tk["prompt"][:60])
        console.print(t)
        return
    if not task:
        task = [typer.prompt("Describe the coding task")]
    prompt = " ".join(task)
    if not workspace_path:
        folders = [f for f in permissions.list_folders() if f.get("active")]
        if not folders:
            console.print("[err]no authorized folders. Add one first:[/err] "
                          "neural-web permissions --add-folder ~/Projects")
            raise typer.Exit(1)
        import questionary
        workspace_path = questionary.select(
            "Choose an authorized workspace:",
            choices=[f["path"] for f in folders]).ask()
    show_banner(console)
    from ..agent_runner import stream_agent_run
    stream_agent_run(prompt, workspace_path, max_steps, model)


@app.command("clear-cache")
def clear_cache():
    """Clear the in-memory page cache and search dedup state."""
    from ...app.scraping import engine as scrape_engine_mod
    scrape_engine_mod._mem_cache.clear()
    scrape_engine_mod._robots_cache.clear()
    console.print("[ok]scrape cache cleared[/ok]")


# `permissions` sub-alias used in help text
permissions_app = app
