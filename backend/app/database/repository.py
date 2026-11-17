"""All SQLite persistence goes through this repository layer."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .database import db, _write_lock
from .models import ExtractedRecord, SearchResult


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_session_id() -> str:
    return uuid.uuid4().hex


# ---------- sessions ----------

def create_session(prompt: str, depth: str, params: dict) -> str:
    sid = new_session_id()
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (id, prompt, depth, status, params_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (sid, prompt, depth, "queued", json.dumps(params), utcnow(), utcnow()),
        )
    return sid


def update_session(session_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, db() as conn:
        conn.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), session_id))


def get_session(session_id: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    return dict(row) if row else None


def list_sessions(limit: int = 100) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, prompt, depth, status, pages_scraped, records_found, created_at, updated_at, error"
            " FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- queries ----------

def log_query(session_id: str, query: str, engine: str, round_no: int, results_count: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO queries (session_id, query, engine, round, results_count, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (session_id, query, engine, round_no, results_count, utcnow()),
        )


def list_queries(session_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT query, engine, round, results_count, created_at FROM queries"
            " WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- urls ----------

def upsert_url(session_id: str, result: SearchResult, round_no: int, status: str = "discovered") -> bool:
    """Insert discovered URL. Returns True if newly inserted, False if duplicate."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO urls (session_id, url, title, snippet, engine, score, status, round, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, result.url, result.title, result.snippet, result.engine,
                 result.score, status, round_no, utcnow()),
            )
        return True
    except Exception:
        return False


def update_url_status(session_id: str, url: str, status: str, error: str = "") -> None:
    with db() as conn:
        conn.execute(
            "UPDATE urls SET status=?, error=? WHERE session_id=? AND url=?",
            (status, error, session_id, url),
        )


def list_urls(session_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT url, title, snippet, engine, score, status, error, round, created_at"
            " FROM urls WHERE session_id=? ORDER BY score DESC, id", (session_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def urls_by_status(session_id: str) -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM urls WHERE session_id=? GROUP BY status",
            (session_id,),
        ).fetchall()
    return {r["status"]: r["c"] for r in rows}


def get_snippet(session_id: str, url: str) -> str:
    """Stored search-engine snippet for a URL (used as honest evidence when
    the page itself cannot be scraped, e.g. robots-blocked / anti-bot)."""
    with db() as conn:
        row = conn.execute(
            "SELECT snippet FROM urls WHERE session_id=? AND url=?",
            (session_id, url)).fetchone()
    return (row["snippet"] or "") if row else ""


# ---------- pages ----------

def save_page(session_id: str, page: dict) -> None:
    from urllib.parse import urlsplit
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pages (session_id, url, final_url, status, title, domain,"
            " method, http_status, raw_text, clean_text, metadata_json, content_hash,"
            " word_count, scraped_at, error)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, page["url"], page.get("final_url", ""), page.get("status", "ok"),
             page.get("title", ""), urlsplit(page.get("final_url") or page["url"]).netloc,
             page.get("method", ""), page.get("http_status"),
             page.get("raw_text", ""), page.get("clean_text", ""),
             json.dumps(page.get("metadata", {})), page.get("content_hash", ""),
             page.get("word_count", 0), utcnow(), page.get("error", "")),
        )


def save_snapshot(page: dict) -> None:
    """Store a page snapshot for change detection."""
    with db() as conn:
        conn.execute(
            "INSERT INTO snapshots (url, content_hash, snapshot_text, captured_at,"
            " word_count, title) VALUES (?,?,?,?,?,?)",
            (page["url"], page.get("content_hash", ""), page.get("clean_text") or page.get("raw_text", ""),
             utcnow(), page.get("word_count", 0), page.get("title", "")),
        )


def latest_snapshot(url: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE url=? ORDER BY id DESC LIMIT 1", (url,)
        ).fetchone()
    return dict(row) if row else None


def count_snapshots(url: str) -> int:
    with db() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM snapshots WHERE url=?", (url,)).fetchone()[0])


def get_page(session_id: str, url: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM pages WHERE session_id=? AND url=?", (session_id, url)
        ).fetchone()
    return dict(row) if row else None


def get_page_hashes(session_id: str) -> set[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT content_hash FROM pages WHERE session_id=? AND content_hash != ''",
            (session_id,),
        ).fetchall()
    return {r["content_hash"] for r in rows}


def list_pages(session_id: str, include_text: bool = False) -> list[dict]:
    cols = "url, final_url, status, title, word_count, content_hash, scraped_at, error"
    if include_text:
        cols += ", raw_text, clean_text, metadata_json"
    with db() as conn:
        rows = conn.execute(f"SELECT {cols} FROM pages WHERE session_id=? ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------- records ----------

def save_record(session_id: str, rec: ExtractedRecord, content_hash: str = "") -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO records (session_id, url, data_json, confidence, verification,"
            " verification_notes, content_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (session_id, rec.url, json.dumps(rec.data, ensure_ascii=False), rec.confidence,
             rec.verification, rec.verification_notes, content_hash, utcnow()),
        )
        return int(cur.lastrowid or 0)


def update_record(record_id: int, confidence: float, verification: str, notes: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE records SET confidence=?, verification=?, verification_notes=? WHERE id=?",
            (confidence, verification, notes, record_id),
        )


def list_records(session_id: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, url, data_json, confidence, verification, verification_notes"
            " FROM records WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["data"] = json.loads(d.pop("data_json"))
        out.append(d)
    return out


# ---------- usage ----------

def log_usage(session_id: Optional[str], model: str, request_type: str,
              prompt_tokens: int, completion_tokens: int, latency_ms: int,
              ok: bool = True, error: str = "", provider: str = "groq") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO usage_log (session_id, provider, model, request_type, prompt_tokens,"
            " completion_tokens, total_tokens, latency_ms, ok, error, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, provider, model, request_type, prompt_tokens, completion_tokens,
             prompt_tokens + completion_tokens, latency_ms, int(ok), error, utcnow()),
        )


def usage_summary() -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT model, SUM(total_tokens) t, COUNT(*) calls,"
            " SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failures FROM usage_log GROUP BY model"
        ).fetchall()
    per_model = {r["model"]: {"tokens": r["t"] or 0, "calls": r["calls"], "failures": r["failures"]}
                 for r in rows}
    return {
        "per_model": per_model,
        "total_tokens": sum(m["tokens"] for m in per_model.values()),
        "total_calls": sum(m["calls"] for m in per_model.values()),
        "total_failures": sum(m["failures"] for m in per_model.values()),
    }


# ---------- app settings ----------

def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---------- dashboard stats ----------

def dashboard_stats() -> dict:
    with db() as conn:
        def one(sql: str) -> int:
            r = conn.execute(sql).fetchone()
            return int(r[0] or 0)
        return {
            "sessions": one("SELECT COUNT(*) FROM sessions"),
            "sessions_ok": one("SELECT COUNT(*) FROM sessions WHERE status='complete'"),
            "sessions_running": one(
                "SELECT COUNT(*) FROM sessions WHERE status IN ('running','queued')"),
            "pages_scraped": one("SELECT COUNT(*) FROM pages WHERE status='ok'"),
            "urls_discovered": one("SELECT COUNT(*) FROM urls"),
            "records_extracted": one("SELECT COUNT(*) FROM records"),
            "ai_calls": one("SELECT COUNT(*) FROM usage_log"),
            "ai_tokens": one("SELECT COALESCE(SUM(total_tokens),0) FROM usage_log"),
            "ai_failures": one("SELECT COUNT(*) FROM usage_log WHERE ok=0"),
        }
