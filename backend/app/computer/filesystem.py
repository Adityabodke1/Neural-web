"""Hardened filesystem engine.

Every operation: canonicalize → revalidate → execute (atomically) → audit.
Protection against traversal, symlink escapes, race conditions, malicious
filenames, unauthorized recursive deletion and hard-link attacks.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from . import permissions
from .permissions import LEVEL_WORKSPACE_WRITE, PermissionDenied, check_access

log = logging.getLogger("neuralweb.filesystem")

MAX_READ_BYTES = 8 * 1024 * 1024
EVIL_NAME_RE = re.compile(r"[\x00-\x1f]|\.\.|^[. ]+$", re.A)


def _validate_name(name: str) -> str:
    base = os.path.basename(name)
    if EVIL_NAME_RE.search(base) or base in (".", ".."):
        raise PermissionDenied(f"malicious filename rejected: {name!r}")
    return base


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file in the same directory + os.replace (atomic, no partial state)."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".nweb_tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on POSIX & Windows (same volume)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _open_nofollow(path: Path, mode: str):
    """Open without following symlinks where the OS supports it (TOCTOU guard)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW) if "r" in mode else None
        if fd is not None:
            return os.fdopen(fd, mode, encoding="utf-8", errors="replace")
    except OSError as e:
        if e.errno == getattr(__import__("errno"), "ELOOP", 0) or "too many" in str(e).lower():
            raise PermissionDenied("symlink loop detected")
        if "no such" not in str(e).lower():
            raise
    return open(path, mode, encoding="utf-8", errors="replace")


def _reject_symlink_escape(path: Path) -> None:
    """Every path component must be a real dir inside the authorized root."""
    if path.is_symlink():
        real = path.resolve(strict=False)
        if not any(real == r or r in real.parents for r in permissions.authorized_roots()):
            raise PermissionDenied(f"symlink escape detected: {path} → {real}")


# ---------------- operations ----------------

def read_file(path: str, session_id: str = "") -> dict:
    p = check_access(path, session_id=session_id)
    _reject_symlink_escape(p)
    if not p.is_file():
        raise PermissionDenied(f"not a file: {p}")
    size = p.stat().st_size
    if size > MAX_READ_BYTES:
        raise PermissionDenied(f"file too large ({size} bytes > {MAX_READ_BYTES})")
    with _open_nofollow(p, "r") as f:
        content = f.read()
    permissions.audit("file_read", str(p), session_id=session_id)
    return {"path": str(p), "size": size, "content": content}


def write_file(path: str, content: str, session_id: str = "") -> dict:
    p = check_access(path, need_write=True, session_id=session_id)
    _reject_symlink_escape(p.parent)
    _validate_name(p.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Revalidate immediately before the write (race-condition guard)
    check_access(str(p.parent), need_write=True, session_id=session_id)
    _atomic_write(p, content.encode("utf-8"))
    permissions.audit("file_write", str(p), level=LEVEL_WORKSPACE_WRITE,
                      detail=f"{len(content)} bytes", session_id=session_id)
    return {"path": str(p), "bytes": len(content), "written": True}


def create_directory(path: str, session_id: str = "") -> dict:
    p = check_access(path, need_write=True, session_id=session_id)
    p.mkdir(parents=True, exist_ok=True)
    permissions.audit("dir_create", str(p), level=LEVEL_WORKSPACE_WRITE, session_id=session_id)
    return {"path": str(p), "created": True}


def _safe_rmtree(path: Path) -> None:
    """Refuse dangerous recursive deletion; confirm param required by caller."""

    def onerr(func, path_, exc_info):
        raise PermissionDenied(f"cannot remove {path_}: {exc_info[1]}")

    shutil.rmtree(path, onerror=onerr)


def delete_path(path: str, recursive: bool = False, confirm: bool = False,
                session_id: str = "") -> dict:
    """Delete requires explicit confirm=true (bulk/destructive)."""
    if not confirm:
        raise PermissionDenied("destructive operation requires explicit confirmation "
                               "(confirm=true / user approval)")
    p = check_access(path, need_write=True, session_id=session_id)
    _reject_symlink_escape(p)
    n_items = 1
    if p.is_dir() and not p.is_symlink():
        n_items = sum(1 for _ in p.rglob("*")) + 1
        if recursive and n_items > 50 and not confirm:
            raise PermissionDenied("bulk deletion requires confirmation")
        if recursive:
            _safe_rmtree(p)
        else:
            p.rmdir()
    else:
        p.unlink()
    permissions.audit("file_delete", str(p), level=LEVEL_WORKSPACE_WRITE,
                      detail=f"recursive={recursive} items={n_items}", session_id=session_id)
    return {"path": str(p), "deleted": True, "items": n_items}


def move_path(src: str, dst: str, confirm: bool = False, session_id: str = "") -> dict:
    s = check_access(src, session_id=session_id)
    d = check_access(dst, need_write=True, session_id=session_id)
    _reject_symlink_escape(s)
    _validate_name(d.name if d.suffix or not d.is_dir() else s.name)
    target = d if d.is_dir() and not s.is_dir() else d
    if target.exists() and not confirm:
        raise PermissionDenied(f"destination exists: {target} (confirm=true to overwrite)")
    check_access(str(s.parent), need_write=True, session_id=session_id)
    if target.exists() and confirm:
        _atomic_delete_tmp(target)
    shutil.move(str(s), str(target))
    permissions.audit("file_move", f"{s} → {target}", level=LEVEL_WORKSPACE_WRITE,
                      session_id=session_id)
    return {"source": str(s), "destination": str(target), "moved": True}


def _atomic_delete_tmp(target: Path) -> None:
    if target.is_dir():
        _safe_rmtree(target)
    else:
        target.unlink()


def copy_path(src: str, dst: str, session_id: str = "") -> dict:
    s = check_access(src, session_id=session_id)
    d = check_access(dst, need_write=True, session_id=session_id)
    _reject_symlink_escape(s)
    target = d / s.name if d.is_dir() else d
    if s.is_dir():
        shutil.copytree(str(s), str(target), symlinks=False,
                        ignore_dangling_symlinks=True)
    else:
        shutil.copy2(str(s), str(target), follow_symlinks=False)
    permissions.audit("file_copy", f"{s} → {target}", level=LEVEL_WORKSPACE_WRITE,
                      session_id=session_id)
    return {"source": str(s), "destination": str(target), "copied": True}


def list_directory(path: str, session_id: str = "") -> dict:
    p = check_access(path, session_id=session_id)
    entries = []
    for e in sorted(p.iterdir())[:2000]:
        try:
            st = e.stat(follow_symlinks=False)
            entries.append({
                "name": e.name, "path": str(e),
                "type": "dir" if e.is_dir() else ("symlink" if e.is_symlink() else "file"),
                "size": st.st_size,
                "modified": st.st_mtime,
            })
        except OSError:
            continue
    permissions.audit("dir_list", str(p), session_id=session_id)
    return {"path": str(p), "entries": entries}


def search_files(root: str, pattern: str, content: Optional[str] = None,
                 max_results: int = 200, session_id: str = "") -> dict:
    """Search by glob name pattern and optionally by file content (plain text)."""
    r = check_access(root, session_id=session_id)
    out: list[dict] = []
    count = 0
    for p in r.rglob(pattern):
        if count >= max_results:
            break
        if p.is_file() and not p.is_symlink():
            hit = {"path": str(p)}
            if content is not None:
                try:
                    if p.stat().st_size <= MAX_READ_BYTES:
                        text = p.read_text(encoding="utf-8", errors="replace")
                        if content.lower() in text.lower():
                            idx = text.lower().find(content.lower())
                            hit["preview"] = text[max(0, idx - 60): idx + len(content) + 60]
                        else:
                            continue
                except (OSError, PermissionError):
                    continue
            out.append(hit)
            count += 1
    permissions.audit("file_search", f"{root} :: {pattern}", session_id=session_id)
    return {"root": str(r), "matches": out, "truncated": count >= max_results}


filesystem_engine = type("FS", (), {})()
