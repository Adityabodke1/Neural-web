"""Central configuration. All values come from environment variables (.env)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels above backend/app/config.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class Settings:
    """Runtime settings, re-read from env on attribute access where useful."""

    project_root: Path = _PROJECT_ROOT
    export_dir: Path = _PROJECT_ROOT / "exports"

    # ---- AI: Groq only ----
    GROQ_API_KEY: str = _env("GROQ_API_KEY")
    GROQ_MODEL: str = _env("GROQ_MODEL") or "openai/gpt-oss-120b"
    GROQ_FALLBACK_MODEL: str = _env("GROQ_FALLBACK_MODEL") or "openai/gpt-oss-20b"
    GROQ_BASE_URL: str = _env("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
    AI_TIMEOUT: float = float(_env("AI_TIMEOUT", "60"))
    AI_MAX_RETRIES: int = int(_env("AI_MAX_RETRIES", "3"))
    AI_MAX_OUTPUT_TOKENS: int = int(_env("AI_MAX_OUTPUT_TOKENS", "4096"))

    # ---- Search ----
    TAVILY_API_KEY: str = _env("TAVILY_API_KEY")
    SEARCH_MAX_RESULTS: int = int(_env("SEARCH_MAX_RESULTS", "8"))

    # ---- Scraping ----
    FIRECRAWL_API_KEY: str = _env("FIRECRAWL_API_KEY")
    SCRAPE_TIMEOUT: float = float(_env("SCRAPE_TIMEOUT", "30"))
    SCRAPE_MAX_BYTES: int = int(_env("SCRAPE_MAX_BYTES", str(3 * 1024 * 1024)))
    MAX_CONCURRENT_SCRAPES: int = int(_env("MAX_CONCURRENT_SCRAPES", "4"))
    PER_HOST_DELAY: float = float(_env("PER_HOST_DELAY", "1.5"))
    USER_AGENT: str = _env("USER_AGENT") or (
        "NeuralWebResearch/1.0 (autonomous research agent; respects robots.txt; "
        "contact: neuralweb@localhost)"
    )

    # ---- Storage ----
    DATABASE_URL: str = _env("DATABASE_URL") or "sqlite:///./neural_web.db"

    # ---- Server ----
    API_HOST: str = _env("API_HOST", "127.0.0.1")
    API_PORT: int = int(_env("API_PORT", "8000"))

    # Shared secret used to sign session ids (prevents guessing other sessions)
    session_secret: str = secrets.token_hex(16)

    @property
    def db_path(self) -> Path:
        url = self.DATABASE_URL
        if url.startswith("sqlite:///"):
            raw = url[len("sqlite:///"):]
            p = Path(raw)
            return p if p.is_absolute() else (_PROJECT_ROOT / raw if not raw.startswith("/") else p)
        raise ValueError(f"Only sqlite:// URLs are supported, got: {url}")

    def has_groq(self) -> bool:
        return bool(self.GROQ_API_KEY)

    def has_tavily(self) -> bool:
        return bool(self.TAVILY_API_KEY)

    def has_firecrawl(self) -> bool:
        return bool(self.FIRECRAWL_API_KEY)


settings = Settings()
