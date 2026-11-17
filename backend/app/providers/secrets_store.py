"""Secure credential storage.

Order of preference:
1. Environment variables (.env) — zero-setup, good for the primary provider.
2. OS keyring (if the `keyring` package and a backend like SecretService exist).
3. SQLite storage — last resort, clearly labelled as less secure; file is
   chmod 600. The DB file never leaves the machine unless the user copies it.
"""
from __future__ import annotations

import logging
import os

from ..config import settings

log = logging.getLogger("neuralweb.secrets")

_keyring = None
try:
    import keyring as _kr
    # Probe for a working backend; headless servers often have none.
    _kr.get_keyring()
    if "fail" not in type(_kr.get_keyring()).__module__.lower():
        _keyring = _kr
except Exception:
    _keyring = None

SERVICE = "neural-web"


def key_from_env(provider_id: str) -> str:
    return {
        "groq": settings.GROQ_API_KEY,
        "gemini": os.environ.get("GEMINI_API_KEY", ""),
        "openai": os.environ.get("OPENAI_API_KEY", ""),
        "openrouter": os.environ.get("OPENROUTER_API_KEY", ""),
        "cerebras": os.environ.get("CEREBRAS_API_KEY", ""),
        "together": os.environ.get("TOGETHER_API_KEY", ""),
        "custom": os.environ.get("CUSTOM_API_KEY", ""),
    }.get(provider_id, "")


def env_var_name(provider_id: str) -> str:
    return f"{provider_id.upper()}_API_KEY"


def _db_key_name(provider_id: str) -> str:
    return f"provider_key_{provider_id}"


def _db_set_key(provider_id: str, api_key: str) -> None:
    from ..database.database import db
    with db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_db_key_name(provider_id), api_key))


def _db_get_key(provider_id: str) -> str:
    from ..database.database import db
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?",
                           (_db_key_name(provider_id),)).fetchone()
    return (row["value"] if row else "")


def store_key(provider_id: str, api_key: str) -> str:
    """Store a key. Returns the storage source label ('keyring' or 'database')."""
    if _keyring is not None:
        try:
            _keyring.set_password(SERVICE, provider_id, api_key)
            return "keyring"
        except Exception as e:
            log.warning("keyring unavailable (%s); storing in database", e)
    _db_set_key(provider_id, api_key)
    return "database"


def get_key(provider_id: str, db_value: str = "") -> str:
    """Resolve a provider key: env → keyring → database → explicit db_value."""
    env = key_from_env(provider_id)
    if env:
        return env
    if _keyring is not None:
        try:
            v = _keyring.get_password(SERVICE, provider_id)
            if v:
                return v
        except Exception:
            pass
    return _db_get_key(provider_id) or db_value


def delete_key(provider_id: str) -> None:
    if _keyring is not None:
        try:
            _keyring.delete_password(SERVICE, provider_id)
        except Exception:
            pass
    from ..database.database import db
    with db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (_db_key_name(provider_id),))


def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:3] + "…" + key[-2:]
    return key[:6] + "…" + key[-4:]


def keyring_available() -> bool:
    return _keyring is not None
