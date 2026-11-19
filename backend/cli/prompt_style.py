"""The glowing NEURAL WEB prompt: brand chip, accent arrow, build folder.

Two styles are exposed:
  * prompt_markup() — prompt_toolkit HTML (interactive shell)
  * rich_prompt()   — Rich markup (chat mode's console.input)

The folder shown is the one the agent is building in: the workspace selected
via /workspace, else the first authorized folder, else the current directory.
"""
from __future__ import annotations

import os
from pathlib import Path

# brand aqua · accent gold · folder pink-purple
BRAND_FG = "#00120F"
BRAND_BG = "#00FFD5"
ARROW = "#FFC857"
FOLDER_FG = "#FF79E8"
FOLDER_BG = "#1C0F2E"


def active_workspace() -> str:
    """Where she is building: selected workspace → authorized folder → cwd."""
    try:
        from .commands import chat_cmd
        sel = chat_cmd.SELECTED_WORKSPACE.get("path")
        if sel:
            return str(sel)
    except Exception:
        pass
    try:
        from ..app.computer import permissions
        folders = [f["path"] for f in permissions.list_folders()
                   if f.get("active")]
        if folders:
            return folders[0]
    except Exception:
        pass
    return os.getcwd()


def shorten_path(path: str, max_len: int = 38) -> str:
    """~/… form, middle-truncated so the prompt never wraps."""
    path = str(path or "")
    home = str(Path.home())
    if path == home:
        path = "~"
    elif path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    if len(path) > max_len:
        keep = max_len - 2
        path = path[: keep // 2] + "…" + path[-(keep - keep // 2):]
    return path


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def prompt_markup(path: str | None = None) -> str:
    """prompt_toolkit HTML for the shell: glowing chip + folder + arrow."""
    folder = _esc(shorten_path(path or active_workspace()))
    return (
        f'<b><style fg="{BRAND_FG}" bg="{BRAND_BG}"> neural-web </style></b>'
        f'<b><style fg="{ARROW}"> ❯ </style></b>'
        f'<b><style fg="{FOLDER_FG}" bg="{FOLDER_BG}"> {folder} </style></b>'
        f'<b><style fg="{BRAND_BG}"> ❯ </style></b>'
    )


def rich_prompt(path: str | None = None) -> str:
    """Rich markup version for console.input (chat mode)."""
    folder = shorten_path(path or active_workspace())
    return (
        f"[bold {BRAND_FG} on {BRAND_BG}] neural-web [/]"
        f"[bold {ARROW}] ❯ [/]"
        f"[bold {FOLDER_FG} on {FOLDER_BG}] {folder} [/]"
        f"[bold {BRAND_BG}]❯ [/]"
    )
