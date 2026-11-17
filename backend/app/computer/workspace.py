"""Project workspace manager: open/create projects, file tree, git checkpoints."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ..database import repository as repo
from . import permissions
from .permissions import LEVEL_WORKSPACE_WRITE, PermissionDenied

log = logging.getLogger("neuralweb.workspace")


def _git(cwd: str, *args: str) -> dict:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
            env={k: v for k, v in __import__("os").environ.items()
                 if not k.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))})
        return {"code": proc.returncode, "out": proc.stdout, "err": proc.stderr}
    except Exception as e:
        return {"code": -1, "out": "", "err": str(e)}


def is_git_repo(workspace: str) -> bool:
    return _git(workspace, "rev-parse", "--is-inside-work-tree")["out"].strip() == "true"


def workspace_status(workspace: str) -> dict:
    permissions.check_access(workspace)
    p = Path(workspace)
    return {
        "path": str(p),
        "name": p.name,
        "os": permissions.detect_os(),
        "authorized": True,
        "git": is_git_repo(workspace) and {
            "branch": _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")["out"].strip(),
            "changes": _git(workspace, "status", "--porcelain")["out"][:5000],
        },
        "languages": _detect_languages(p),
    }


def _detect_languages(p: Path) -> list[str]:
    exts = {suffix.lstrip(".").lower() for f in list(p.rglob("*"))[:800]
            for suffix in [f.suffix] if f.is_file()}
    found = []
    markers = {"py": "Python", "js": "JavaScript", "jsx": "React", "ts": "TypeScript",
               "tsx": "React/TS", "java": "Java", "c": "C", "cpp": "C++", "h": "C/C++",
               "rs": "Rust", "go": "Go", "rb": "Ruby", "php": "PHP", "cs": "C#",
               "html": "HTML", "css": "CSS", "json": "JSON", "md": "Markdown"}
    for e, name in markers.items():
        if e in exts:
            found.append(name)
    return found


def file_tree(workspace: str, depth: int = 3, max_entries: int = 400) -> dict:
    permissions.check_access(workspace)

    def walk(dir_path: Path, level: int) -> list[dict]:
        if level > depth:
            return []
        out = []
        try:
            entries = sorted(dir_path.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return []
        for e in entries[:max_entries]:
            if e.name in (".git", "node_modules", "__pycache__", ".venv", "venv",
                          "dist", ".next", "target"):
                continue
            entry: dict = {"name": e.name, "path": str(e),
                           "type": "dir" if e.is_dir() else "file"}
            if e.is_dir():
                entry["children"] = walk(e, level + 1)
            out.append(entry)
        return out

    return {"root": workspace, "tree": walk(Path(workspace), 1)}


def create_project(path: str, name: str, template: str = "python") -> dict:
    p = permissions.check_access(path, need_write=True)
    proj = p / name
    if proj.exists():
        raise PermissionDenied(f"path already exists: {proj}")
    proj.mkdir(parents=True)
    if template == "python":
        (proj / "main.py").write_text('def main():\n    print("hello from %s")\n\n\n'
                                      'if __name__ == "__main__":\n    main()\n' % name)
        (proj / "requirements.txt").write_text("")
    elif template == "node":
        (proj / "package.json").write_text(
            '{"name": "%s", "version": "0.1.0", "private": true, '
            '"scripts": {"start": "node index.js"}}' % name)
        (proj / "index.js").write_text('console.log("hello from %s");\n' % name)
    (proj / ".gitignore").write_text("__pycache__/\nnode_modules/\n.env\n")
    permissions.audit("project_created", str(proj), level=LEVEL_WORKSPACE_WRITE)
    return {"path": str(proj), "template": template}


def checkpoint(workspace: str, message: str = "NEURAL WEB checkpoint") -> dict:
    """Git checkpoint before major modifications (never commits without a repo)."""
    permissions.check_access(workspace, need_write=True)
    if not is_git_repo(workspace):
        if _git(workspace, "init")["code"] != 0:
            return {"checkpoint": False, "reason": "git unavailable"}
        _git(workspace, "config", "user.email", "neuralweb@local")
        _git(workspace, "config", "user.name", "NEURAL WEB")
    _git(workspace, "add", "-A")
    res = _git(workspace, "commit", "-m", message, "--allow-empty")
    permissions.audit("git_checkpoint", workspace, level=LEVEL_WORKSPACE_WRITE,
                      detail=message)
    return {"checkpoint": res["code"] == 0, "output": (res["out"] or res["err"])[:500]}


def restore_checkpoint(workspace: str, ref: str = "HEAD~1") -> dict:
    permissions.check_access(workspace, need_write=True)
    if not is_git_repo(workspace):
        raise PermissionDenied("not a git repository; no checkpoints available")
    res = _git(workspace, "reset", "--hard", ref)
    permissions.audit("git_restore", workspace, level=LEVEL_WORKSPACE_WRITE, detail=ref)
    return {"restored": res["code"] == 0, "output": (res["out"] or res["err"])[:500]}


def git_diff(workspace: str) -> dict:
    permissions.check_access(workspace)
    return {"diff": _git(workspace, "diff")["out"][:50_000] or
                    _git(workspace, "diff", "--staged")["out"][:50_000]}


workspace_manager = type("WS", (), {})()
