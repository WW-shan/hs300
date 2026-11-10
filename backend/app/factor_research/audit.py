"""Batch data audits for the factor research layer."""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.fingerprints import hash_token, sha256_json, utc_now
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.models import AuditResult, FactorAuditRecord, ResearchPolicy


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _classify_stock(panel: pl.DataFrame, factor_id: str) -> tuple[str, float, float, float, float, int]:
    work = panel.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("_date"),
    ).filter(pl.col(factor_id).is_not_null())
    if work.is_empty() or "_date" not in work.columns:
        return "unknown", 0.0, 0.0, 0.0, 0.0, 0
    grouped = work.group_by("_date").agg(
        pl.len().alias("n"),
        pl.col(factor_id).n_unique().alias("unique_n"),
    )
    valid_dates = grouped.height
    median_n = float(grouped["n"].median() or 0.0)
    median_unique = float(grouped["unique_n"].median() or 0.0)
    total_dates = panel.select(pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("_date")).n_unique()
    active_ratio = valid_dates / max(total_dates, 1)
    if valid_dates == 0:
        kind = "unknown"
    elif median_unique <= 1.0 and median_n >= 2:
        kind = "market_level"
    elif median_n >= 2 and median_unique >= 2:
        kind = "stock_cross_sectional"
    elif active_ratio < 0.10 or (median_n < 2 and float(grouped["n"].max() or 0) >= 2):
        kind = "event_sparse"
    else:
        kind = "unknown"
    confidence = min(1.0, max(0.0, active_ratio) * min(1.0, median_n / 30.0))
    return kind, confidence, active_ratio, median_n, median_unique, valid_dates


def _audit_one(panel: pl.DataFrame, factor_id: str, policy: ResearchPolicy) -> FactorAuditRecord:
    if "date" not in panel.columns or "symbol" not in panel.columns:
        return FactorAuditRecord(factor_id=factor_id, available=False, reasons=["panel lacks date/symbol"])
    if factor_id not in panel.columns:
        return FactorAuditRecord(factor_id=factor_id, available=False, reasons=["factor column missing"])

    work = panel.with_columns(
        pl.col("date").cast(pl.Utf8).str.slice(0, 10).alias("_date"),
    )
    non_null = work.filter(pl.col(factor_id).is_not_null())
    if non_null.is_empty():
        return FactorAuditRecord(factor_id=factor_id, available=False, reasons=["all values are null"])

    kind, confidence, active_ratio, median_n, median_unique, valid_dates = _classify_stock(work, factor_id)
    latest = str(non_null["_date"].max())
    latest_date = _as_date(latest)
    staleness = (date.today() - latest_date).days if latest_date is not None else None
    ratio = len(non_null) / max(len(work), 1)
    reasons: list[str] = []
    if valid_dates < int(policy.sample_sufficiency.get("rank_min_valid_cross_sections_recent", 40)):
        reasons.append("valid_cross_sections_below_recent_minimum")
    if ratio < float(policy.sample_sufficiency.get("minimum_non_null_ratio", 0.10)):
        reasons.append("non_null_ratio_below_minimum")
    if staleness is not None and staleness > int(policy.staleness_trading_days.get("price", 10)):
        reasons.append("latest_value_is_stale")
    return FactorAuditRecord(
        factor_id=factor_id,
        available=True,
        latest_date=latest,
        staleness_days=staleness,
        non_null_ratio=round(ratio, 6),
        active_date_ratio=round(active_ratio, 6),
        valid_dates=valid_dates,
        median_non_null_symbols=round(median_n, 6),
        median_unique_values=round(median_unique, 6),
        cross_section_type=kind,  # type: ignore[arg-type]
        classification_confidence=round(confidence, 6),
        reasons=reasons,
    )


class AuditService:
    def __init__(self, data_dir: Path, policy: ResearchPolicy) -> None:
        self.data_dir = Path(data_dir)
        self.policy = policy

    def audit_panel(
        self,
        panel: pl.DataFrame,
        factor_ids: list[str],
        *,
        generated_at: str | None = None,
        input_fingerprints: Mapping[str, Any] | None = None,
        set_id: str | None = None,
        set_version: int | None = None,
        active_gate: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        factors = {factor_id: _audit_one(panel, factor_id, self.policy) for factor_id in factor_ids}
        findings: dict[str, dict[str, Any]] = {}
        for factor_id, item in factors.items():
            if not item.available:
                integrity = "unknown"
                overall = "unknown"
            elif item.reasons:
                integrity = "unknown"
                overall = "watch_only"
            else:
                integrity = "pass"
                overall = "watch_only"
            findings[factor_id] = {
                "integrity": integrity,
                "signal": "unassessed",
                "incremental": "unknown",
                "investability": "not_tested",
                "overall": overall,
                "reasons": item.reasons,
            }
        payload = {
            "schema_version": 1,
            "policy_id": self.policy.policy_id,
            "policy": self.policy.model_dump(mode="json"),
            "factors": {key: value.model_dump(mode="json") for key, value in factors.items()},
            "findings": findings,
            "input_fingerprints": dict(input_fingerprints or {}),
            "set_id": set_id,
            "set_version": set_version,
            "active_gate": dict(active_gate) if active_gate is not None else None,
        }
        audit_id = sha256_json(payload)
        result = AuditResult(
            audit_id=audit_id,
            generated_at=generated_at or utc_now(),
            policy_id=self.policy.policy_id,
            factors=factors,
            findings=findings,
        )
        self._save(
            result,
            input_fingerprints=dict(input_fingerprints or {}),
            set_id=set_id,
            set_version=set_version,
            active_gate=dict(active_gate) if active_gate is not None else None,
        )
        return result

    def _save(
        self,
        result: AuditResult,
        *,
        input_fingerprints: dict[str, Any],
        set_id: str | None = None,
        set_version: int | None = None,
        active_gate: dict[str, Any] | None = None,
    ) -> None:
        root = self.data_dir / "factor_research" / "audits" / hash_token(result.audit_id)
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            return
        atomic_write_json(manifest_path, {
            "schema_version": 1,
            "audit_id": result.audit_id,
            "generated_at": result.generated_at,
            "policy_id": result.policy_id,
            "policy_sha256": sha256_json(self.policy.model_dump(mode="json")),
            "input_fingerprints": input_fingerprints,
            "factor_count": len(result.factors),
            "set_id": set_id,
            "set_version": set_version,
            "active_gate": active_gate,
        })
        atomic_write_json(root / "metrics.json", {
            "audit_id": result.audit_id,
            "factors": {key: value.model_dump(mode="json") for key, value in result.factors.items()},
        })
        atomic_write_json(root / "findings.json", result.findings)
        lines = [f"# Factor Audit {result.audit_id}", "", f"Policy: `{result.policy_id}`", ""]
        for factor_id, finding in result.findings.items():
            lines.append(f"- `{factor_id}`: integrity={finding['integrity']}, overall={finding['overall']}")
        atomic_write_text(root / "report.md", "\n".join(lines) + "\n")


def load_audit(data_dir: Path, audit_id: str) -> dict[str, Any] | None:
    root = Path(data_dir) / "factor_research" / "audits" / hash_token(audit_id)
    manifest = root / "manifest.json"
    findings = root / "findings.json"
    if not manifest.exists() or not findings.exists():
        return None
    return {
        "manifest": json.loads(manifest.read_text(encoding="utf-8")),
        "findings": json.loads(findings.read_text(encoding="utf-8")),
    }
