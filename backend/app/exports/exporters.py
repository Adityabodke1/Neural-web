"""Export engines: JSON, CSV, Excel. All exports include source URLs and
per-field provenance."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import settings
from ..database import repository as repo

log = logging.getLogger("neuralweb.exports")


def _flat_record(rec: dict) -> dict:
    """Flatten a provenance record for tabular export."""
    data = rec.get("data", {})
    row: dict = {"entity": data.get("entity_name") or ""}
    for k, f in (data.get("fields") or {}).items():
        v = f.get("value")
        if isinstance(v, (list, dict)):
            v = "; ".join(map(str, v)) if isinstance(v, list) else json.dumps(v)
        row[k] = v
        row[f"{k}__source_url"] = f.get("source_url")
        row[f"{k}__evidence"] = (f.get("evidence") or "")[:200]
        row[f"{k}__verification"] = f.get("verification")
        row[f"{k}__confidence"] = f.get("confidence")
    row["record_sources"] = "; ".join(data.get("sources", []))
    row["record_verification"] = rec.get("verification", "")
    row["record_confidence"] = rec.get("confidence", "")
    row["record_id"] = rec.get("id")
    return row


def export_session(session_id: str, fmt: str) -> dict:
    """Write an export file and return its path. fmt: json|csv|xlsx"""
    session = repo.get_session(session_id)
    if not session:
        raise ValueError("session not found")
    records = repo.list_records(session_id)
    pages = repo.list_pages(session_id)
    queries = repo.list_queries(session_id)
    settings.export_dir.mkdir(exist_ok=True)

    base = f"neuralweb_{session_id[:8]}"
    if fmt == "json":
        payload = {
            "session": {"id": session_id, "prompt": session["prompt"],
                        "depth": session["depth"], "status": session["status"],
                        "created_at": session["created_at"]},
            "queries": queries,
            "sources": [{"url": p["url"], "title": p["title"], "final_url": p.get("final_url"),
                         "status": p["status"], "word_count": p["word_count"],
                         "content_hash": p["content_hash"], "scraped_at": p["scraped_at"]}
                        for p in pages],
            "records": [{"id": r["id"], **r["data"], "source_url": r["url"],
                         "confidence": r["confidence"], "verification": r["verification"],
                         "verification_notes": r["verification_notes"]} for r in records],
            "report_markdown": session.get("report_md") or "",
        }
        path = settings.export_dir / f"{base}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), "format": "json", "records": len(records)}

    rows = [_flat_record(r) for r in records]
    if fmt == "csv":
        import csv
        path = settings.export_dir / f"{base}.csv"
        fieldnames = sorted({k for r in rows for k in r}) or ["entity"]
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return {"path": str(path), "format": "csv", "records": len(rows)}

    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "Extracted Data"
        fieldnames = sorted({k for r in rows for k in r}) or ["entity"]
        ws.append(fieldnames)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", start_color="1F3A5F")
        for r in rows:
            ws.append([r.get(k) for k in fieldnames])
        ws2 = wb.create_sheet("Sources")
        ws2.append(["url", "title", "status", "word_count", "content_hash", "scraped_at"])
        for p in pages:
            ws2.append([p["url"], p["title"], p["status"], p["word_count"],
                        p["content_hash"], p["scraped_at"]])
        ws3 = wb.create_sheet("Queries")
        ws3.append(["query", "engine", "round", "results", "created_at"])
        for q in queries:
            ws3.append([q["query"], q["engine"], q["round"], q["results_count"],
                        q["created_at"]])
        if session.get("report_md"):
            ws4 = wb.create_sheet("Report")
            for line in (session["report_md"] or "").splitlines():
                ws4.append([line[:32767]])
        path = settings.export_dir / f"{base}.xlsx"
        wb.save(path)
        return {"path": str(path), "format": "xlsx", "records": len(rows)}

    raise ValueError(f"unsupported format: {fmt}")
