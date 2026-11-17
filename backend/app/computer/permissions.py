"""Folder-based permission manager.

- The user explicitly authorizes folders; everything inside is allowed for
  read/write without per-file prompts.
- Nothing outside authorized folders is touched without explicit approval.
- Protected OS/system/credential locations are always denied.
- Path traversal and symlink escapes are blocked via realpath containment.
- Every filesystem/terminal/system action is written to the audit log.
- Level 3 (system administration) operations require explicit approval via
  pending_approvals (CLI: `neural-web permissions approve <id>`).
"""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Optional

from ..database.database import db
from ..database import repository as repo

log = logging.getLogger("neuralweb.permissions")

LEVEL_READ = 1
LEVEL_WORKSPACE_WRITE = 2
LEVEL_SYSTEM_ADMIN = 3

_LEVEL_NAMES = {1: "READ", 2: "WORKSPACE_WRITE", 3: "SYSTEM_ADMIN"}

# Locations that can never be touched via folder authorization. On top of the
# literal entries, first-path-segment matches (e.g. /etc/...) are denied too.
PROTECTED_PATHS = [
    "/etc", "/boot", "/root", "/proc", "/sys", "/dev", "/usr", "/bin", "/sbin",
    "/lib", "/lib64", "/var/log", "/var/lib", "/opt", "/srv",
    "~/.ssh", "~/.gnupg", "~/.password-store", "~/.password-store-legacy",
    "~/.keepassxc", "~/.mozilla", "~/.config/google-chrome",
    "~/.config/chromium", "~/.config/BraveSoftware", "~/.azure", "~/.aws",
    "~/.kube", "~/.docker",
    "C:/Windows", "C:/Program Files", "C:/Program Files (x86)",
    "C:/ProgramData", "C:/Users/%USER%/AppData/Local/Google",
    "C:/Users/%USER%/AppData/Roaming/Mozilla",
]
DANGEROUS_CMD_PATTERNS = [
    r"\brm\s+-rf\s+/(?:\s|$)", r"\bmkfs\b", r"\bdd\s+if=/dev/(?:zero|null)\s+of=/dev/",
    r":(){ :|:& };:", r"\bchmod\s+-R\s+777\s+/\s", r"\bshutdown\b", r"\breboot\b",
    r"\bkill\s+-1\s+1\b", r">\s*/dev/sd[a-z]", r"\bformat\s+[cC]:",
    r"\bRemove-Item\s+-Recurse\s+-Force\s+[cC]:\\",
]


class PermissionDenied(Exception):
    pass


def detect_os() -> str:
    s = platform.system().lower()
    return {"linux": "linux", "windows": "windows", "darwin": "macos"}.get(s, s)


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p))).resolve()


def _protected_hit(path: Path) -> bool:
    ps = str(path).replace("\\", "/").lower()
    user_home = Path.home().as_posix().lower()
    for raw in PROTECTED_PATHS:
        cand = raw.replace("%USER%", Path.home().name).lower()
        if cand.startswith("~"):
            cand = user_home + cand[1:]
        if ps == cand or ps.startswith(cand + "/") or ps.startswith(cand + "\\"):
            return True
    return False


# ---------------- audit ----------------

def audit(action: str, target: str = "", allowed: bool = True, level: int = LEVEL_READ,
          detail: str = "", session_id: str = "") -> None:
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, allowed, level, detail,"
                " session_id) VALUES (?,?,?,?,?,?,?,?)",
                (repo.utcnow(), "agent", action, target[:500], int(allowed), level,
                 detail[:1000], session_id))
    except Exception as e:  # audit must never break operations
        log.warning("audit log write failed: %s", e)


def list_audit(limit: int = 200) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------- folder authorization ----------------

def authorize_folder(path: str, label: str = "") -> dict:
    p = _expand(path)
    if _protected_hit(p):
        raise PermissionDenied(f"refusing to authorize protected location: {p}")
    if not p.exists() or not p.is_dir():
        raise PermissionDenied(f"not an existing directory: {p}")
    if str(p) in ("/", str(Path.home().anchor or "/")):
        raise PermissionDenied("refusing to authorize the filesystem root")
    with db() as conn:
        conn.execute(
            "INSERT INTO authorized_folders (path, label, os, active, added_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET active=1, label=excluded.label",
            (str(p), label or p.name, detect_os(), 1, repo.utcnow()))
    audit("folder_authorized", str(p), level=LEVEL_WORKSPACE_WRITE,
          detail=f"OS={detect_os()}")
    return get_folder(str(p))


def get_folder(path: str) -> dict:
    canon = str(_expand(path))
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM authorized_folders WHERE path=?", (canon,)).fetchone()
    d = dict(row) if row else {"path": canon, "authorized": False}
    if row:
        with db() as conn:
            stats = conn.execute(
                "SELECT COUNT(*) accesses, MAX(ts) last_activity FROM audit_log"
                " WHERE target LIKE ? AND allowed=1", (canon + "%",)).fetchone()
        d.update({"authorized": True,
                  "files_accessed": stats["accesses"] or 0,
                  "last_activity": stats["last_activity"],
                  "active": bool(d["active"])})
    return d


def list_folders() -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM authorized_folders ORDER BY id DESC").fetchall()
    return [get_folder(r["path"]) for r in rows]


def revoke_folder(path: str) -> bool:
    canon = str(_expand(path))
    with db() as conn:
        cur = conn.execute(
            "UPDATE authorized_folders SET active=0 WHERE path=?", (canon,))
        ok = cur.rowcount > 0
    if ok:
        audit("folder_revoked", canon, level=LEVEL_WORKSPACE_WRITE)
    return ok


def authorized_roots(active_only: bool = True) -> list[Path]:
    with db() as conn:
        q = "SELECT path FROM authorized_folders WHERE active=1" if active_only else \
            "SELECT path FROM authorized_folders"
        rows = conn.execute(q).fetchall()
    return [_expand(r["path"]) for r in rows]


# ---------------- path checks ----------------

def _inside(child: Path, parent: Path) -> bool:
    try:
        return child == parent or parent in child.parents
    except Exception:
        return False


def check_access(path: str, need_write: bool = False,
                 session_id: str = "") -> Path:
    """Canonical permission check. Raises PermissionDenied when not allowed."""
    raw = Path(os.path.expandvars(os.path.expanduser(path)))
    p = raw.resolve(strict=False)
    if _protected_hit(p):
        audit("fs_denied_protected", str(p), allowed=False,
              level=LEVEL_WORKSPACE_WRITE, session_id=session_id)
        raise PermissionDenied(f"protected location: {p}")
    # symlink escape: any parent that is a symlink must still resolve inside a root
    roots = authorized_roots()
    allowed = any(_inside(p, r) for r in roots)
    if not allowed:
        audit("fs_denied_unauthorized", str(p), allowed=False,
              level=LEVEL_WORKSPACE_WRITE, session_id=session_id)
        raise PermissionDenied(
            f"'{p}' is outside all authorized folders. "
            "Authorize it first (Add Authorized Folder) and retry.")
    if need_write:
        # For files that don't exist yet, writability means the nearest
        # EXISTING ancestor directory is writable — os.access is False for
        # missing paths, and nested paths may need several levels created.
        write_target = p if p.exists() else p.parent
        while not write_target.exists() and write_target != write_target.parent:
            write_target = write_target.parent
        if not os.access(write_target, os.W_OK):
            audit("fs_denied_no_write", str(p), allowed=False, session_id=session_id)
            raise PermissionDenied(f"no write permission for: {p}")
    audit("fs_ok" if not need_write else "fs_write_ok", str(p),
          level=LEVEL_WORKSPACE_WRITE, session_id=session_id)
    return p


def check_command_safety(command: str) -> tuple[bool, str]:
    """Policy validation for model-generated commands. Returns (allowed, reason)."""
    for pat in DANGEROUS_CMD_PATTERNS:
        import re
        if re.search(pat, command):
            return False, f"command matches dangerous pattern {pat!r}"
    return True, ""


def is_command_in_workspace(command: str, cwd: str) -> bool:
    """Terminal commands must run inside an authorized workspace."""
    try:
        check_access(cwd, need_write=False)
        return True
    except PermissionDenied:
        return False


# ---------------- level-3 approvals ----------------

def request_approval(kind: str, description: str, payload: dict | None = None) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO pending_approvals (kind, description, payload_json, created_at)"
            " VALUES (?,?,?,?)",
            (kind, description, __import__("json").dumps(payload or {}), repo.utcnow()))
        aid = int(cur.lastrowid or 0)
    audit("approval_requested", kind, allowed=False, level=LEVEL_SYSTEM_ADMIN,
          detail=description)
    return aid


def decide_approval(approval_id: int, approve: bool) -> bool:
    with db() as conn:
        cur = conn.execute(
            "UPDATE pending_approvals SET status=?, decided_at=? WHERE id=? AND status='pending'",
            ("approved" if approve else "denied", repo.utcnow(), approval_id))
        ok = cur.rowcount > 0
    audit("approval_" + ("granted" if approve else "denied"), f"approval#{approval_id}",
          level=LEVEL_SYSTEM_ADMIN)
    return ok


def get_approval(approval_id: int) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM pending_approvals WHERE id=?",
                           (approval_id,)).fetchone()
    return dict(row) if row else None


def list_approvals(status: str = "pending") -> list[dict]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM pending_approvals WHERE status=? ORDER BY id DESC",
                            (status,)).fetchall()
    return [dict(r) for r in rows]


def require_approved(kind: str, description: str, payload: dict | None = None) -> int:
    """Blocking helper: create a pending approval and raise until it is granted."""
    aid = request_approval(kind, description, payload)
    raise PermissionDenied(
        f"Operation requires explicit approval (#{aid}): {description}. "
        f"Approve with: neural-web permissions approve {aid}")


def level_name(level: int) -> str:
    return _LEVEL_NAMES.get(level, str(level))
