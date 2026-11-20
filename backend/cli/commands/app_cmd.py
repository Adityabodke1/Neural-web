"""`neural-web app` — launch the desktop app (local UI) in its own window.

Serves the FastAPI backend + built frontend on localhost, then opens it as a
standalone app window (Chrome/Brave/Chromium `--app=`) or the default browser.
All functionality is the same engine the CLI uses: chat, coding agent,
research, sources, models — plus the in-app folder picker.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import typer
from rich.panel import Panel

from ..theme import console

_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
_BROWSERS = ("brave-browser", "brave", "google-chrome", "google-chrome-stable",
             "chromium", "chromium-browser", "microsoft-edge")


def _wait_health(url: str, timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _open_window(url: str) -> str:
    """Standalone window when a Chromium-family browser exists."""
    for name in _BROWSERS:
        exe = shutil.which(name)
        if exe:
            try:
                subprocess.Popen(
                    [exe, f"--app={url}", "--window-size=1360,900",
                     "--class=NeuralWeb", "--name=NeuralWeb"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return name
            except Exception:
                continue
    try:
        webbrowser.open(url)
        return "browser"
    except Exception:
        return ""


def app(
    port: int = typer.Option(8555, "--port", "-p", help="Local port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (keep localhost)"),
    no_window: bool = typer.Option(False, "--no-window", help="Don't open a window"),
):
    """Open the NEURAL WEB desktop app (chat, coding agent, research, sources)."""
    import uvicorn

    if not (_DIST / "index.html").exists():
        console.print("[err]frontend build missing[/err] — run: "
                      "[cyan]cd frontend && npm install && npm run build[/cyan]")
        raise typer.Exit(1)
    if not _DIST.joinpath("assets").exists():
        console.print("[warn]no frontend assets found — rebuild with "
                      "`cd frontend && npm run build`[/warn]")

    url = f"http://{host}:{port}"
    config = uvicorn.Config("backend.app.main:app", host=host, port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="neural-web-app",
                              daemon=True)
    thread.start()

    if not _wait_health(url):
        console.print("[err]server did not come up — is the port busy?[/err] "
                      f"try: neural-web app --port {port + 1}")
        raise typer.Exit(1)

    target = ""
    if not no_window:
        target = _open_window(url)

    console.print(Panel(
        f"[bold]NEURAL WEB app[/bold] is running at [cyan]{url}[/cyan]\n"
        f"[muted]window: {target or 'not opened'} · folder picker lives under "
        f"the chat box · Ctrl+C here to quit[/muted]",
        border_style="cyan"))
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        console.print("[muted]closing app…[/muted]")
        server.should_exit = True
        thread.join(timeout=5)
