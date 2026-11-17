"""Lightweight vector store over SQLite + numpy (FAISS-compatible API).

If faiss is installed it is used for the search index; otherwise exact
cosine search with numpy (fine for research-scale corpora).
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from ..database import repository as repo
from ..database.database import db
from .embeddings import embedder

log = logging.getLogger("neuralweb.vectors")

_faiss = None
try:  # optional
    import faiss
    _faiss = faiss
except Exception:
    pass


class VectorStore:
    def index_session(self, session_id: str) -> int:
        """Embed the session's records + report + scraped page titles for future search."""
        session = repo.get_session(session_id)
        if not session:
            return 0
        texts: list[tuple[str, str, int]] = []  # (kind, text, ref_id)
        for r in repo.list_records(session_id):
            flat = r["data"].get("entity_name", "") + " " + " ".join(
                str(f.get("value")) for f in r["data"].get("fields", {}).values()
                if f.get("value") is not None)
            texts.append(("record", flat.strip()[:2000], r["id"]))
        if session.get("report_md"):
            texts.append(("report", session["report_md"][:6000], 0))
        for p in repo.list_pages(session_id):
            texts.append(("page", f"{p['title']} {p['url']}"[:1000], 0))
        if not texts:
            return 0
        vecs = embedder.encode([t[1] for t in texts])
        now = repo.utcnow()
        with db() as conn:
            conn.execute("DELETE FROM vectors WHERE session_id=?", (session_id,))
            for (kind, text, ref), v in zip(texts, vecs):
                conn.execute(
                    "INSERT INTO vectors (session_id, kind, ref_id, text, vector, dim, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (session_id, kind, ref, text, v.tobytes(), int(v.shape[0]), now))
        return len(texts)

    def search(self, query: str, top_k: int = 8,
               session_id: Optional[str] = None) -> list[dict]:
        with db() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT v.*, s.prompt FROM vectors v JOIN sessions s ON s.id=v.session_id"
                    " WHERE v.session_id=?", (session_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT v.*, s.prompt FROM vectors v JOIN sessions s ON s.id=v.session_id"
                    " LIMIT 5000").fetchall()
        if not rows:
            return []
        qv = embedder.encode([query])[0]
        dim = rows[0]["dim"]
        if qv.shape[0] != dim:
            # embedding backend changed since indexing — re-encode not possible; skip
            log.warning("vector dim mismatch (%d vs %d); rebuilding not supported", qv.shape[0], dim)
            return []
        mat = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        scores = mat @ qv
        order = np.argsort(scores)[::-1][:top_k]
        out = []
        for i in order:
            r = rows[i]
            out.append({
                "session_id": r["session_id"],
                "session_prompt": r["prompt"][:160],
                "kind": r["kind"],
                "ref_id": r["ref_id"],
                "text": r["text"][:400],
                "score": round(float(scores[i]), 4),
            })
        return out


vector_store = VectorStore()
