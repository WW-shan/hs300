"""Batch data audits for the factor research layer."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.fingerprints import hash_token, sha256_file, sha256_json, utc_now
from app.factor_research.locking import atomic_write_json, atomic_write_text
from app.factor_research.models import (
    AuditResult,
    CrossSectionType,
    FactorAuditRecord,
    ResearchPolicy,
)
from app.factor_research.references import load_field_taxonomy

_DAILY_STATS_SCHEMA = {
    "factor_id": pl.String,
    "date": pl.Date,
    "total_rows": pl.UInt32,
    "non_null_count": pl.UInt32,
    "informative_count": pl.UInt32,
    "unique_count": pl.UInt32,
}
_EXT_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CROSS_SECTION_TYPES = {
    "stock_cross_sectional",
    "industry_cross_sectional",
    "event_sparse",
    "market_level",
    "text_or_signal",
    "unknown",
}
_DATA_CLASSES = {"price", "sentiment", "flow", "industry", "macro", "financial", "unknown"}
_PIT_STATUSES = {"verified", "declared", "not_applicable", "unknown"}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _empty_daily_stats() -> pl.DataFrame:
    return pl.DataFrame(schema=_DAILY_STATS_SCHEMA)


def _normalise_panel_dates(panel: pl.DataFrame) -> pl.DataFrame:
    dtype = panel.schema["date"]
    if dtype == pl.Date:
        date_expr = pl.col("date")
    elif isinstance(dtype, pl.Datetime):
        date_expr = pl.col("date").dt.date()
    else:
        date_expr = pl.col("date").cast(pl.Utf8).str.slice(0, 10).str.to_date(strict=False)
    return panel.with_columns(date_expr.alias("_date"))


def _panel_daily_stats(
    panel: pl.DataFrame,
    factor_ids: list[str],
    taxonomy: Mapping[str, Mapping[str, Any]],
) -> tuple[pl.DataFrame, list[date]]:
    work = _normalise_panel_dates(panel)
    date_counts = work.group_by("_date").agg(pl.len().alias("total_rows")).drop_nulls("_date")
    panel_dates = sorted(item for item in date_counts["_date"].to_list() if item is not None)
    parts: list[pl.DataFrame] = []
    for factor_id in factor_ids:
        if factor_id not in work.columns:
            continue
        non_null = pl.col(factor_id).is_not_null()
        informative = non_null
        if bool(taxonomy.get(factor_id, {}).get("zero_is_missing", False)):
            informative = informative & (pl.col(factor_id) != 0)
        grouped = work.group_by("_date").agg([
            non_null.sum().cast(pl.UInt32).alias("non_null_count"),
            informative.sum().cast(pl.UInt32).alias("informative_count"),
            pl.col(factor_id).filter(non_null).n_unique().cast(pl.UInt32).alias("unique_count"),
        ]).drop_nulls("_date")
        part = (
            grouped.join(date_counts, on="_date", how="left")
            .select([
                pl.lit(factor_id).alias("factor_id"),
                pl.col("_date").alias("date"),
                pl.col("total_rows"),
                pl.col("non_null_count"),
                pl.col("informative_count"),
                pl.col("unique_count"),
            ])
        )
        parts.append(part)
    if not parts:
        return _empty_daily_stats(), panel_dates
    return pl.concat(parts, how="vertical_relaxed"), panel_dates


def _scan_ext_daily_stats(
    config_path: Path,
    factor_to_field: Mapping[str, str],
    taxonomy: Mapping[str, Mapping[str, Any]],
    *,
    config_reference: str,
) -> tuple[pl.DataFrame, list[date], dict[str, Any]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, Mapping) or config.get("mode") != "timeseries":
        raise ValueError(f"extension config is not timeseries: {config_path}")
    root = config_path.parent / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(f"extension timeseries partitions not found: {root}")
    schema = pl.scan_parquet([str(path) for path in paths]).collect_schema()
    has_date = "date" in schema.names()
    scan = pl.scan_parquet([str(path) for path in paths], include_file_paths="_source_path")
    if has_date:
        date_expr = pl.col("date").cast(pl.Date)
    else:
        date_expr = (
            pl.col("_source_path")
            .str.extract(r"date=(\d{4}-\d{2}-\d{2})")
            .str.to_date(strict=False)
        )

    selected: list[str] = ["symbol"]
    aggs: list[pl.Expr] = [pl.len().alias("_total_rows")]
    for factor_id, field in factor_to_field.items():
        if field not in schema.names():
            continue
        selected.append(field)
        non_null = pl.col(field).is_not_null()
        informative = non_null
        if bool(taxonomy.get(factor_id, {}).get("zero_is_missing", False)):
            informative = informative & (pl.col(field) != 0)
        aggs.extend([
            non_null.sum().cast(pl.UInt32).alias(f"_{field}__non_null"),
            informative.sum().cast(pl.UInt32).alias(f"_{field}__informative"),
            pl.col(field).filter(non_null).n_unique().cast(pl.UInt32).alias(f"_{field}__unique"),
        ])

    wide = (
        scan.select([*selected, "_source_path"])
        .with_columns(date_expr.alias("date"))
        .drop_nulls("date")
        .group_by("date")
        .agg(aggs)
        .sort("date")
        .collect()
    )
    panel_dates = wide["date"].to_list()
    parts: list[pl.DataFrame] = []
    for factor_id, field in factor_to_field.items():
        if field not in schema.names():
            continue
        parts.append(wide.select([
            pl.lit(factor_id).alias("factor_id"),
            pl.col("date"),
            pl.col("_total_rows").alias("total_rows"),
            pl.col(f"_{field}__non_null").alias("non_null_count"),
            pl.col(f"_{field}__informative").alias("informative_count"),
            pl.col(f"_{field}__unique").alias("unique_count"),
        ]))
    daily = pl.concat(parts, how="vertical_relaxed") if parts else _empty_daily_stats()
    fingerprint = {
        "config_path": config_reference,
        "config_sha256": sha256_file(config_path),
        "partition_count": len(paths),
        "first_partition": paths[0].parent.name,
        "last_partition": paths[-1].parent.name,
    }
    return daily, panel_dates, fingerprint


def _median(values: pl.Series) -> float:
    if values.is_empty():
        return 0.0
    return float(values.median() or 0.0)


def _taxonomy_value(
    taxonomy: Mapping[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    value = str(taxonomy.get(key, default))
    return value if value in allowed else default


def _observed_cross_section(median_unique: float, median_repeat_ratio: float) -> CrossSectionType:
    if median_unique <= 1.0:
        return "market_level"
    if 2.0 <= median_unique <= 100.0 and median_repeat_ratio >= 0.50:
        return "industry_cross_sectional"
    return "stock_cross_sectional"


def _audit_from_daily_stats(
    stats: pl.DataFrame | None,
    *,
    factor_id: str,
    panel_dates: list[date],
    taxonomy: Mapping[str, Any],
    policy: ResearchPolicy,
) -> FactorAuditRecord:
    taxonomy_record = dict(taxonomy)
    data_class = _taxonomy_value(taxonomy_record, "data_class", _DATA_CLASSES, "unknown")
    pit_status = _taxonomy_value(taxonomy_record, "pit_hint", _PIT_STATUSES, "unknown")
    prior_cross_section = _taxonomy_value(
        taxonomy_record, "cross_section_type", _CROSS_SECTION_TYPES, "unknown"
    )
    zero_is_missing = bool(taxonomy_record.get("zero_is_missing", False))
    confidence = float(taxonomy_record.get("classification_confidence", 0.0) or 0.0)
    classification_ref = sha256_json(taxonomy_record) if taxonomy_record else None
    common = {
        "factor_id": factor_id,
        "classification_ref": classification_ref,
        "data_class": data_class,
        "pit_status": pit_status,
        "zero_is_missing": zero_is_missing,
    }
    if stats is None or stats.is_empty():
        return FactorAuditRecord(
            **common,
            available=False,
            cross_section_type=prior_cross_section,
            classification_confidence=round(confidence, 6),
            reasons=["no factor observations"],
        )

    ordered = stats.sort("date")
    non_null_dates = ordered.filter(pl.col("non_null_count") > 0)
    informative_dates = ordered.filter(pl.col("informative_count") > 0)
    if non_null_dates.is_empty():
        return FactorAuditRecord(
            **common,
            available=False,
            cross_section_type=prior_cross_section,
            classification_confidence=round(confidence, 6),
            reasons=["all values are null"],
        )

    latest_non_null = str(non_null_dates["date"].max())
    total_rows = int(ordered["total_rows"].sum() or 0)
    non_null_count = int(ordered["non_null_count"].sum() or 0)
    informative_count = int(ordered["informative_count"].sum() or 0)
    non_null_ratio = non_null_count / max(total_rows, 1)
    informative_ratio = informative_count / max(total_rows, 1)
    reasons: list[str] = []

    if informative_dates.is_empty():
        return FactorAuditRecord(
            **common,
            available=True,
            latest_date=latest_non_null,
            latest_non_null_date=latest_non_null,
            latest_informative_date=None,
            staleness_days=None,
            non_null_ratio=round(non_null_ratio, 6),
            informative_ratio=0.0,
            cross_section_type=prior_cross_section,
            classification_confidence=round(confidence, 6),
            reasons=["no informative values after zero-missing treatment"],
        )

    latest_informative = str(informative_dates["date"].max())
    latest_informative_date = _as_date(latest_informative)
    staleness = (
        sum(1 for item in panel_dates if latest_informative_date is not None and item > latest_informative_date)
        if latest_informative_date is not None
        else None
    )
    valid_dates = informative_dates.height
    active_date_ratio = valid_dates / max(len(panel_dates), 1)
    median_n = _median(informative_dates["informative_count"])
    median_unique = _median(informative_dates["unique_count"])
    repeat_values = informative_dates.filter(pl.col("non_null_count") > 0).select(
        (1.0 - pl.col("unique_count") / pl.col("non_null_count")).alias("repeat_ratio")
    )["repeat_ratio"]
    median_repeat_ratio = _median(repeat_values)
    observed_cross_section = _observed_cross_section(median_unique, median_repeat_ratio)

    if prior_cross_section == "unknown":
        cross_section_type = observed_cross_section
    else:
        cross_section_type = prior_cross_section
    mismatch = False
    if (prior_cross_section == "market_level" and observed_cross_section != "market_level") or (prior_cross_section == "industry_cross_sectional" and observed_cross_section not in {
        "industry_cross_sectional", "market_level"
    }) or (
        prior_cross_section == "stock_cross_sectional"
        and observed_cross_section == "market_level"
        and median_unique <= 1.0
        and median_n > 2.0
    ):
        mismatch = True
    if mismatch:
        reasons.append("classification_mismatch")

    if data_class == "unknown":
        reasons.append("data_class_unknown")
    if pit_status == "unknown":
        reasons.append("pit_status_unknown")

    recent_minimum = int(
        policy.sample_sufficiency.get("rank_min_valid_cross_sections_recent", 40)
    )
    if valid_dates < recent_minimum:
        reasons.append("valid_cross_sections_below_recent_minimum")

    if cross_section_type in {"stock_cross_sectional", "event_sparse"}:
        if cross_section_type == "event_sparse":
            minimum_symbols = float(policy.sample_sufficiency.get("event_min_informative_symbols_per_date", 3))
        else:
            minimum_symbols = float(
                policy.sample_sufficiency.get("rank_min_non_null_symbols_per_date", 30)
            )
        if median_n < minimum_symbols:
            reasons.append("insufficient_cross_section_width")
    elif cross_section_type == "industry_cross_sectional":
        minimum_groups = float(
            policy.sample_sufficiency.get("industry_min_valid_groups_per_date", 5)
        )
        if median_unique < minimum_groups:
            reasons.append("insufficient_industry_groups")
    if cross_section_type != "event_sparse":
        minimum_ratio = float(policy.sample_sufficiency.get("minimum_non_null_ratio", 0.10))
        if non_null_ratio < minimum_ratio:
            reasons.append("non_null_ratio_below_minimum")
    if cross_section_type == "event_sparse" and valid_dates < int(
        policy.sample_sufficiency.get("event_min_valid_dates", 40)
    ):
        reasons.append("event_valid_dates_below_minimum")

    override = taxonomy_record.get("staleness_days_override")
    threshold = (
        int(override)
        if isinstance(override, int) and not isinstance(override, bool) and override > 0
        else int(policy.staleness_trading_days.get(data_class, 0) or 0)
    )
    if threshold <= 0:
        reasons.append("staleness_threshold_unavailable")
    elif staleness is not None and staleness > threshold:
        reasons.append("latest_value_is_stale")

    return FactorAuditRecord(
        **common,
        available=True,
        latest_date=latest_informative,
        latest_non_null_date=latest_non_null,
        latest_informative_date=latest_informative,
        staleness_days=staleness,
        non_null_ratio=round(non_null_ratio, 6),
        informative_ratio=round(informative_ratio, 6),
        active_date_ratio=round(active_date_ratio, 6),
        valid_dates=valid_dates,
        median_non_null_symbols=round(median_n, 6),
        median_unique_values=round(median_unique, 6),
        cross_section_type=cross_section_type,  # type: ignore[arg-type]
        classification_confidence=round(confidence if not mismatch else min(confidence, 0.5), 6),
        reasons=reasons,
    )


class AuditService:
    def __init__(
        self,
        data_dir: Path,
        policy: ResearchPolicy,
        *,
        taxonomy: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.policy = policy
        self.taxonomy = {
            str(key): dict(value)
            for key, value in (taxonomy if taxonomy is not None else load_field_taxonomy(self.data_dir)).items()
        }

    def _build_result(
        self,
        stats_by_factor: Mapping[str, pl.DataFrame],
        panel_dates: list[date],
        factor_ids: list[str],
        *,
        generated_at: str | None = None,
        input_fingerprints: Mapping[str, Any] | None = None,
        set_id: str | None = None,
        set_version: int | None = None,
        active_gate: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        factors = {
            factor_id: _audit_from_daily_stats(
                stats_by_factor.get(factor_id),
                factor_id=factor_id,
                panel_dates=panel_dates,
                taxonomy=self.taxonomy.get(factor_id, {}),
                policy=self.policy,
            )
            for factor_id in factor_ids
        }
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
        if "date" not in panel.columns:
            raise ValueError("panel lacks date column")
        daily, panel_dates = _panel_daily_stats(panel, factor_ids, self.taxonomy)
        stats_by_factor = {
            factor_id: daily.filter(pl.col("factor_id") == factor_id)
            for factor_id in factor_ids
        }
        return self._build_result(
            stats_by_factor,
            panel_dates,
            factor_ids,
            generated_at=generated_at,
            input_fingerprints=input_fingerprints,
            set_id=set_id,
            set_version=set_version,
            active_gate=active_gate,
        )

    def audit_ext_data(
        self,
        ext_id: str,
        factor_ids: list[str] | None = None,
        *,
        generated_at: str | None = None,
        set_id: str | None = None,
        set_version: int | None = None,
        active_gate: Mapping[str, Any] | None = None,
    ) -> AuditResult:
        if not _EXT_ID_RE.fullmatch(ext_id):
            raise ValueError(f"invalid extension id: {ext_id}")
        config_path = self.data_dir / "ext_data" / ext_id / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        config = json.loads(config_path.read_text(encoding="utf-8"))
        fields = config.get("fields") if isinstance(config, Mapping) else None
        if not isinstance(fields, list):
            raise ValueError(f"extension config has no fields: {config_path}")
        all_factor_to_field = {
            f"ext_{ext_id}_{field['name']}": str(field["name"])
            for field in fields
            if isinstance(field, Mapping) and isinstance(field.get("name"), str)
        }
        selected_ids = list(factor_ids) if factor_ids is not None else list(all_factor_to_field)
        factor_to_field = {factor_id: all_factor_to_field[factor_id] for factor_id in selected_ids if factor_id in all_factor_to_field}
        daily, panel_dates, source_fingerprint = _scan_ext_daily_stats(
            config_path,
            factor_to_field,
            self.taxonomy,
            config_reference=config_path.relative_to(self.data_dir).as_posix(),
        )
        stats_by_factor = {
            factor_id: daily.filter(pl.col("factor_id") == factor_id)
            if factor_id in factor_to_field
            else _empty_daily_stats()
            for factor_id in selected_ids
        }
        return self._build_result(
            stats_by_factor,
            panel_dates,
            selected_ids,
            generated_at=generated_at,
            input_fingerprints={"ext_data": source_fingerprint},
            set_id=set_id,
            set_version=set_version,
            active_gate=active_gate,
        )

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
        for factor_id, item in result.factors.items():
            finding = result.findings[factor_id]
            reasons = ", ".join(item.reasons) if item.reasons else "none"
            lines.append(
                f"- `{factor_id}`: data_class={item.data_class}, cross_section={item.cross_section_type}, "
                f"integrity={finding['integrity']}, reasons={reasons}"
            )
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
