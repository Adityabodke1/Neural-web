"""Embedding backend for local semantic memory.

Prefers sentence-transformers when installed (better quality); otherwise falls
back to a fast, dependency-light hashed n-gram embedder running on numpy.
Both produce L2-normalised float32 vectors persisted as blobs in SQLite.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Optional

import numpy as np

log = logging.getLogger("neuralweb.embeddings")

_DIM = 384
_ST_MODEL: Optional[object] = None
_ST_NAME = "all-MiniLM-L6-v2"

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = set("the a an and or of to in for on with at by from is are was were be as it its "
            "this that these those you your we our their his her not no but if then".split())


def _ngrams(tokens: list[str], n: int = 2) -> list[str]:
    out = list(tokens)
    out += ["_".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
    return out


class HashingEmbedder:
    """Deterministic feature-hashing embedder: no model download, decent recall."""

    name = "hashing-ngram"

    def _one(self, text: str) -> np.ndarray:
        vec = np.zeros(_DIM, dtype=np.float32)
        tokens = [t for t in _WORD_RE.findall(text.lower()) if t not in _STOP]
        feats = _ngrams(tokens)
        if not feats:
            return vec
        for f in feats:
            h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
            idx = h % _DIM
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign * (1.0 / math.sqrt(len(feats)))
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self._one(t[:4000]) for t in texts])


class SentenceTransformerEmbedder:
    name = f"sentence-transformers/{_ST_NAME}"

    def __init__(self, model) -> None:
        self.model = model

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


def get_embedder():
    """Return the best available embedder (singleton)."""
    global _ST_MODEL
    if _ST_MODEL is not None:
        return _ST_MODEL
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(_ST_NAME)
        _ST_MODEL = SentenceTransformerEmbedder(model)
        log.info("semantic memory: sentence-transformers loaded")
    except Exception as e:
        log.info("semantic memory: using hashing embedder (%s)", type(e).__name__)
        _ST_MODEL = HashingEmbedder()
    return _ST_MODEL


embedder = get_embedder()
