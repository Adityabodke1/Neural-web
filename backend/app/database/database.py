"""SQLite connection management and schema initialisation."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
import os

from ..config import settings

_local = threading.local()
_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    depth TEXT NOT NULL DEFAULT 'standard',
    status TEXT NOT NULL DEFAULT 'queued',
    params_json TEXT NOT NULL DEFAULT '{}',
    plan_json TEXT,
    report_md TEXT,
    report_json TEXT,
    error TEXT,
    pages_scraped INTEGER NOT NULL DEFAULT 0,
    records_found INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    query TEXT NOT NULL,
    engine TEXT NOT NULL,
    round INTEGER NOT NULL DEFAULT 1,
    results_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    url TEXT NOT NULL,
    title TEXT,
    snippet TEXT,
    engine TEXT,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'discovered',
    error TEXT,
    round INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, url)
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    url TEXT NOT NULL UNIQUE,
    final_url TEXT,
    status TEXT NOT NULL,
    title TEXT,
    domain TEXT,
    method TEXT,
    http_status INTEGER,
    raw_text TEXT,
    clean_text TEXT,
    metadata_json TEXT,
    content_hash TEXT,
    word_count INTEGER NOT NULL DEFAULT 0,
    scraped_at TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    url TEXT,
    data_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    verification TEXT NOT NULL DEFAULT 'unverified',
    verification_notes TEXT,
    content_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    provider TEXT NOT NULL DEFAULT 'groq',
    model TEXT NOT NULL,
    request_type TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    ok INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    kind TEXT NOT NULL,
    ref_id INTEGER,
    text TEXT NOT NULL,
    vector BLOB NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'openai_compat',
    base_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    key_source TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_models (
    id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL REFERENCES providers(id),
    model_id TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    task TEXT,
    context_tokens INTEGER,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL,
    UNIQUE(provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS quotas (
    model_ref TEXT PRIMARY KEY,
    limit_tokens INTEGER,
    period TEXT NOT NULL DEFAULT 'manual',
    is_manual INTEGER NOT NULL DEFAULT 1,
    provider_reported INTEGER NOT NULL DEFAULT 0,
    reset_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref TEXT NOT NULL,
    capability TEXT NOT NULL,
    score REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'measured',
    detail TEXT,
    tested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS authorized_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    label TEXT,
    os TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'agent',
    action TEXT NOT NULL,
    target TEXT,
    allowed INTEGER NOT NULL DEFAULT 1,
    level INTEGER NOT NULL DEFAULT 1,
    detail TEXT,
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS coding_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    plan_json TEXT,
    summary TEXT,
    files_touched INTEGER NOT NULL DEFAULT 0,
    commands_run INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    description TEXT NOT NULL,
    payload_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_urls_session ON urls(session_id);
CREATE INDEX IF NOT EXISTS idx_pages_session ON pages(session_id);
CREATE INDEX IF NOT EXISTS idx_records_session ON records(session_id);
CREATE INDEX IF NOT EXISTS idx_queries_session ON queries(session_id);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot_text TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    word_count INTEGER NOT NULL DEFAULT 0,
    title TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_url ON snapshots(url, captured_at DESC);
"""


def _connect() -> sqlite3.Connection:
    db_path: Path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db() -> sqlite3.Connection:
    """One connection per thread, reused."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def db():
    """Yield a connection; commits on success, rolls back on error."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    with _write_lock, db() as conn:
        conn.executescript(SCHEMA)
    # Restrictive permissions on the database (it may contain credential refs)
    try:
        p = settings.db_path
        os.chmod(p, 0o600)
    except OSError:
        pass


def close_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
