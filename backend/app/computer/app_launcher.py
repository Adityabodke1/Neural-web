"""GUI application launching for the voice/computer agent.

GUI apps CANNOT run inside the bwrap sandbox (X11/Wayland need the host
session), so launching is deliberately outside it — but strictly limited to
a whitelist of well-known desktop applications. Arbitrary binaries, shell
metacharacters and file paths are refused. Every launch is audited.

The launched process runs detached (start_new_session) so it survives the
agent run and doesn't inherit the CLI's stdio.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess

from . import permissions

# name the agent may say → actual desktop command
LAUNCHABLE_APPS: dict[str, str] = {
    "brave": "brave-browser",
    "brave-browser": "brave-browser",
    "chrome": "google-chrome-stable",
    "google-chrome": "google-chrome-stable",
    "chromium": "chromium",
    "firefox": "firefox",
    "files": "nautilus",
    "file-manager": "nautilus",
    "nautilus": "nautilus",
    "folder": "nautilus",
    "calculator": "gnome-calculator",
    "text-editor": "gnome-text-editor",
    "gedit": "gedit",
    "editor": "gnome-text-editor",
    "terminal": "x-terminal-emulator",
    "code": "code",
    "vscode": "code",
    "visual-studio-code": "code",
    "settings": "gnome-control-center",
    "music": "rhythmbox",
    "videos": "totem",
    "paint": "gimp",
    "gimp": "gimp",
    "libreoffice": "libreoffice",
    "writer": "libreoffice --writer",
    "calc": "libreoffice --calc",
    "spotify": "spotify",
    "vlc": "vlc",
}

# tokens allowed as extra args (URLs, search words, simple paths) — no shell
# metacharacters ever reach a shell because argv is passed as a list, but this
# also blocks option-injection style weirdness beyond simple flags
_ARG_OK = re.compile(r"^[A-Za-z0-9_.:/?&=%+@~#'\-]+$")
_MAX_ARGS = 8


def resolve_app(name: str) -> str:
    """Whitelist lookup. Returns the real command, '' when refused."""
    key = (name or "").strip().lower().replace(" ", "-")
    return LAUNCHABLE_APPS.get(key, "")


def launch(app: str, args: list[str] | None = None,
           session_id: str = "") -> dict:
    """Launch a whitelisted desktop app on the user's computer. Never raises."""
    cmd = resolve_app(app)
    if not cmd:
        permissions.audit("launch_refused", f"app={app!r}", allowed=False,
                          level=2, session_id=session_id)
        known = ", ".join(sorted(set(LAUNCHABLE_APPS.values()))[:14])
        return {"error": "app_not_allowed",
                "message": f"'{app}' is not on the launch whitelist. "
                           f"Available: {known}"}

    parts = cmd.split() + [str(a) for a in (args or [])][:_MAX_ARGS]
    for a in parts[1:]:
        if not _ARG_OK.match(a) or len(a) > 2000:
            permissions.audit("launch_refused", f"arg={a[:80]!r}", allowed=False,
                              level=2, session_id=session_id)
            return {"error": "invalid_arg",
                    "message": f"argument refused: {a[:80]!r}"}
    if not shutil.which(parts[0]):
        return {"error": "not_installed",
                "message": f"{parts[0]} is not installed on this computer"}

    env = dict(os.environ)          # host env: DISPLAY/WAYLAND/XDG needed
    env.setdefault("DISPLAY", ":0")
    try:
        subprocess.Popen(parts, env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
    except Exception as e:
        permissions.audit("launch_failed", f"{app}: {e}", allowed=False,
                          level=2, session_id=session_id)
        return {"error": "launch_failed", "message": str(e)[:200]}

    permissions.audit("app_launched", " ".join(parts)[:120], allowed=True,
                      level=2, session_id=session_id)
    return {"launched": True, "app": parts[0], "args": parts[1:]}
