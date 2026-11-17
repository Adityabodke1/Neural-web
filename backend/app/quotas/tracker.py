"""Token quota tracking.

Principles:
- Never invent limits. If no provider API and no manual limit exist → 'unavailable'.
- Used tokens come from the usage_log (provider-reported usage metadata).
- Manual limits are clearly labelled; provider-reported quota (OpenRouter
  credits) is labelled as such. Lifetime usage and current-period usage are
  tracked separately and never conflated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..database.database import db
from ..database import repository as repo
from ..providers import manager as pm
from ..providers.adapter import AdapterError

log = logging.getLogger("neuralweb.quotas")


class QuotaTracker:
    # ---------------- usage ----------------

    def used_tokens(self, model_ref: str, period_hours: int | None = None) -> dict:
        """Token consumption for a model. period_hours=None → lifetime."""
        with db() as conn:
            if period_hours:
                since = (datetime.now(timezone.utc) - timedelta(hours=period_hours)).isoformat(
                    timespec="seconds")
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_tokens),0) t, COUNT(*) calls,"
                    " SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) ok_calls,"
                    " SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failed_calls"
                    " FROM usage_log WHERE provider||'/'||model=? AND created_at>=?",
                    (model_ref, since)).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_tokens),0) t, COUNT(*) calls,"
                    " SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) ok_calls,"
                    " SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failed_calls"
                    " FROM usage_log WHERE provider||'/'||model=?", (model_ref,)).fetchone()
        return {"tokens": int(row["t"] or 0), "calls": int(row["calls"] or 0),
                "ok_calls": int(row["ok_calls"] or 0), "failed_calls": int(row["failed_calls"] or 0)}

    # ---------------- quota ----------------

    def set_manual_limit(self, model_ref: str, limit_tokens: int,
                         period: str = "manual", reset_at: str | None = None) -> None:
        with db() as conn:
            conn.execute(
                "INSERT INTO quotas (model_ref, limit_tokens, period, is_manual,"
                " provider_reported, reset_at, updated_at) VALUES (?,?,?,?,0,?,?)"
                " ON CONFLICT(model_ref) DO UPDATE SET limit_tokens=excluded.limit_tokens,"
                " period=excluded.period, is_manual=1, reset_at=excluded.reset_at,"
                " updated_at=excluded.updated_at",
                (model_ref, limit_tokens, period, 1, reset_at, repo.utcnow()))

    def clear_limit(self, model_ref: str) -> None:
        with db() as conn:
            conn.execute("DELETE FROM quotas WHERE model_ref=?", (model_ref,))

    def get_quota(self, model_ref: str) -> dict:
        """Return quota status for one model ref."""
        quota_row = self._quota_row(model_ref)
        used = self.used_tokens(model_ref)
        lifetime = self.used_tokens(model_ref, period_hours=None)
        out: dict = {
            "model_ref": model_ref,
            "used_tokens": used["tokens"],
            "used_calls": used["calls"],
            "ok_calls": used["ok_calls"],
            "failed_calls": used["failed_calls"],
            "period": "lifetime",
            "source": "local-usage-log",
            "limit_tokens": None,
            "remaining_tokens": None,
            "percent_used": None,
            "reset_at": None,
        }
        if quota_row and quota_row["limit_tokens"]:
            used_in_period = self.used_tokens(model_ref, None)["tokens"]
            if quota_row["period"] in ("daily",):
                used_in_period = self.used_tokens(model_ref, 24)["tokens"]
            elif quota_row["period"] in ("monthly",):
                used_in_period = self.used_tokens(model_ref, 24 * 30)["tokens"]
            limit = int(quota_row["limit_tokens"])
            out.update({
                "limit_tokens": limit,
                "used_tokens": used_in_period,
                "remaining_tokens": max(0, limit - used_in_period),
                "percent_used": round(100.0 * used_in_period / limit, 1) if limit else None,
                "period": quota_row["period"],
                "source": "manual-configured" if quota_row["is_manual"] else "unknown",
                "reset_at": quota_row["reset_at"],
            })
        return out

    async def refresh_provider_quota(self, provider_id: str) -> list[dict]:
        """Ask the provider for real quota/credit data (only OpenRouter supports it now)."""
        try:
            adapter = pm._adapter_for(provider_id)
        except AdapterError as e:
            return [{"provider": provider_id, "error": str(e)}]
        reported = adapter.provider_reported_quota() if hasattr(adapter, "provider_reported_quota") else None
        if not reported:
            return [{"provider": provider_id,
                     "note": "no quota API available for this provider — set a manual limit"}]
        with db() as conn:
            rows = conn.execute("SELECT id FROM ai_models WHERE provider_id=?", (provider_id,)).fetchall()
        results = []
        for r in rows:
            ref = r["id"]
            with db() as conn:
                conn.execute(
                    "INSERT INTO quotas (model_ref, limit_tokens, period, is_manual,"
                    " provider_reported, updated_at) VALUES (?,?,?,?,1,?)"
                    " ON CONFLICT(model_ref) DO UPDATE SET limit_tokens=excluded.limit_tokens,"
                    " provider_reported=1, is_manual=0, updated_at=excluded.updated_at",
                    (ref, int(reported["limit"]), "credits", repo.utcnow()))
            results.append({"provider": provider_id, "model_ref": ref, **reported})
        return results

    def _quota_row(self, model_ref: str) -> dict | None:
        with db() as conn:
            row = conn.execute("SELECT * FROM quotas WHERE model_ref=?", (model_ref,)).fetchone()
        return dict(row) if row else None

    # ---------------- dashboard ----------------

    def overview(self, provider_id: str | None = None) -> dict:
        models = pm.list_models(provider_id)
        per_model = []
        for m in models:
            q = self.get_quota(m["id"])
            per_model.append({
                "model_ref": m["id"],
                "provider": m["provider_id"],
                "label": m["label"],
                "is_default": bool(m["is_default"]),
                "enabled": bool(m["enabled"]),
                "quota": q,
            })
        comparable = [m for m in per_model if m["quota"]["limit_tokens"]]
        total = {
            "models_with_limits": len(comparable),
            "models_without_limits": len(per_model) - len(comparable),
        }
        if comparable and len({m["quota"]["period"] for m in comparable}) == 1 and \
                all(m["provider"] == comparable[0]["provider"] for m in comparable):
            limit = sum(m["quota"]["limit_tokens"] for m in comparable)
            used = sum(m["quota"]["used_tokens"] for m in comparable)
            total.update({"aggregated": True, "limit_tokens": limit, "used_tokens": used,
                          "remaining_tokens": max(0, limit - used),
                          "percent_used": round(100.0 * used / limit, 1) if limit else None,
                          "period": comparable[0]["quota"]["period"]})
        else:
            total.update({"aggregated": False,
                          "note": "quotas are not comparable across providers/periods; "
                                  "aggregate view hidden on purpose"})
        return {"models": per_model, "total": total}


quota_tracker = QuotaTracker()
