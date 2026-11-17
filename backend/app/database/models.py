"""Typed row models (dataclasses) shared across modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    score: float = 0.0
    published: Optional[str] = None


@dataclass
class PageContent:
    url: str
    final_url: str = ""
    status: str = "ok"          # ok | failed | duplicate | blocked
    title: str = ""
    raw_text: str = ""          # full visible text
    clean_text: str = ""        # trafilatura main-content extraction
    markdown: str = ""
    metadata: dict = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    content_hash: str = ""
    word_count: int = 0
    error: str = ""


@dataclass
class ExtractedRecord:
    data: dict[str, Any]
    url: str
    confidence: float = 0.5
    verification: str = "unverified"
    verification_notes: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "source_url": self.url,
            "confidence": round(self.confidence, 3),
            "verification": self.verification,
            "verification_notes": self.verification_notes,
        }


@dataclass
class ResearchPlan:
    objective: str = ""
    restated_request: str = ""
    sub_questions: list[str] = field(default_factory=list)
    target_fields: list[str] = field(default_factory=list)
    strategy: str = ""
    entity_type: str = ""
