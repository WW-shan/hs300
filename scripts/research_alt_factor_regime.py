#!/usr/bin/env python3
"""Evaluate audit-passing market-level factors against forward HS300 returns."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.backtest import stats_v2
from app.factor_research.audit import load_audit
from app.factor_research.catalog import load_latest_catalog
from app.factor_research.fingerprints import hash_token, sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.models import FactorAuditRecord
from app.factor_research.signal_scan import time_series_rank_regression

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "alt_hs300_regime_5d_20d_2026-11-10_v2.json"
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
RECENT_START = date(2025, 10, 14)
METHOD_VERSION = "regime_scan_v2"


def _load_audit(data_dir: Path, audit_id: str) -> tuple[dict[str, FactorAuditRecord], dict[str, dict[str, Any]], dict[str, Any]]:
    loaded = load_audit(data_dir, audit_id)
    if loaded is None:
        raise FileNotFoundError(audit_id)
    metrics_path = data_dir / "factor_research" / "audits" / hash_token(audit_id) / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    factors = {
        str(factor_id): FactorAuditRecord.model_validate(payload)
        for factor_id, payload in metrics.get("factors", {}).items()
    }
    return factors, loaded["findings"], loaded["manifest"]


def _market_factor_frame(factor_to_field: dict[str, str]) -> tuple[pl.DataFrame, dict[str, int], int]:
    root = DATA / "ext_data" / "alt_hs300" / "timeseries"
    paths = sorted(root.glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    aggs: list[pl.Expr] = []
    for field in factor_to_field.values():
        values = pl.col(field).drop_nulls()
        aggs.extend([
            values.first().alias(field),
            values.n_unique().alias(f"{field}__unique"),
        ])
    daily = (
        pl.scan_parquet([str(path) for path in paths], include_file_paths="_source_path")
        .with_columns(
            pl.col("_source_path")
            .str.extract(r"date=(\d{4}-\d{2}-\d{2})")
            .str.to_date(strict=False)
            .alias("date")
        )
        .drop_nulls("date")
        .group_by("date")
        .agg(aggs)
        .sort("date")
        .collect()
    )
    uniqueness = {
        factor_id: int(daily[f"{field}__unique"].max() or 0)
        for factor_id, field in factor_to_field.items()
    }
    keep = ["date", *factor_to_field.values()]
    return daily.select(keep), uniqueness, len(paths)


def _index_forward_returns(start: date, end: date) -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("kline_index_daily")
    index = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .sort("date")
        .collect()
        .with_columns([
            (pl.col("close").shift(-5) / pl.col("close") - 1.0).alias("forward_return_5d"),
            (pl.col("close").shift(-20) / pl.col("close") - 1.0).alias("forward_return_20d"),
        ])
        .filter((pl.col("date") >= start) & (pl.col("date") <= end))
    )
    return index


def _qvalues(results: dict[str, dict[str, Any]], horizon: int) -> None:
    p_values = [
        results[factor_id]["horizons"][str(horizon)]["full"]["p_value"]
        for factor_id in results
    ]
    q_values = stats_v2.bh_fdr_qvalues(p_values)
    for factor_id, q_value in zip(results, q_values, strict=True):
        results[factor_id]["horizons"][str(horizon)]["q_value"] = (
            round(float(q_value), 8) if q_value is not None else None
        )


def _tier(horizons: dict[str, Any]) -> str:
    five = horizons["5"]
    twenty = horizons["20"]
    five_beta = five["full"]["directional_beta"]
    twenty_beta = twenty["full"]["directional_beta"]
    if five_beta is None or twenty_beta is None:
        return "inconclusive"
    if (
        five["q_value"] is not None
        and five["q_value"] <= 0.10
        and five_beta > 0
        and (five["recent"]["directional_beta"] or 0.0) > 0
        and twenty_beta > 0
    ):
        return "promising_conditioner"
    if five_beta > 0 and twenty_beta > 0:
        return "watch_conditioner"
    if five_beta < 0 and twenty_beta < 0:
        return "inverse_direction"
    return "weak_or_inconclusive"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--end", default=str(DEFAULT_END))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    audit_factors, findings, manifest = _load_audit(DATA, args.audit_id)
    catalog = load_latest_catalog(DATA)
    if catalog is None:
        raise FileNotFoundError("factor catalog latest pointer missing")
    catalog_by_id = {item.factor_id: item for item in catalog.factors}
    eligible = sorted(
        factor_id
        for factor_id, item in audit_factors.items()
        if findings.get(factor_id, {}).get("integrity") == "pass"
        and item.available
        and item.cross_section_type == "market_level"
    )
    config = json.loads((DATA / "ext_data" / "alt_hs300" / "config.json").read_text(encoding="utf-8"))
    field_by_name = {str(field["name"]): str(field["name"]) for field in config["fields"]}
    factor_to_field = {
        factor_id: field_by_name[factor_id.removeprefix("ext_alt_hs300_")]
        for factor_id in eligible
    }
    market, uniqueness, partition_count = _market_factor_frame(factor_to_field)
    index = _index_forward_returns(start, end)
    joined = market.join(index, on="date", how="inner").sort("date")
    if joined.is_empty():
        raise RuntimeError("market factor and index panel has no overlap")

    results: dict[str, dict[str, Any]] = {}
    for factor_id, field in factor_to_field.items():
        direction = catalog_by_id[factor_id].direction
        sign = -1 if direction == "low" else 1
        horizon_results: dict[str, Any] = {}
        for horizon in (5, 20):
            return_col = f"forward_return_{horizon}d"
            full = time_series_rank_regression(joined, field, return_col, lag=horizon)
            recent_frame = joined.filter(pl.col("date") >= RECENT_START)
            recent = time_series_rank_regression(recent_frame, field, return_col, lag=horizon)
            horizon_results[str(horizon)] = {
                "full": {
                    **full,
                    "directional_beta": round(full["beta"] * sign, 6) if full["beta"] is not None else None,
                },
                "recent": {
                    **recent,
                    "directional_beta": round(recent["beta"] * sign, 6) if recent["beta"] is not None else None,
                },
                "q_value": None,
            }
        results[factor_id] = {
            "factor_id": factor_id,
            "data_class": audit_factors[factor_id].data_class,
            "pit_status": audit_factors[factor_id].pit_status,
            "direction": direction,
            "max_values_per_date": uniqueness[factor_id],
            "horizons": horizon_results,
        }
    for horizon in (5, 20):
        _qvalues(results, horizon)
    for item in results.values():
        item["tier"] = _tier(item["horizons"])

    ranked = sorted(
        results.values(),
        key=lambda item: -abs(item["horizons"]["5"]["full"]["directional_beta"] or 0.0),
    )
    payload = {
        "schema_version": 1,
        "research_id": sha256_json({
            "audit_id": args.audit_id,
            "start": str(start),
            "end": str(end),
            "factors": eligible,
            "kind": "regime_timeseries",
            "method_version": METHOD_VERSION,
        }),
        "generated_at": utc_now(),
        "audit_id": args.audit_id,
        "audit_generated_at": manifest.get("generated_at"),
        "catalog_snapshot_id": catalog.snapshot_id,
        "period": {"start": str(start), "end": str(end), "recent_start": str(RECENT_START)},
        "benchmark": "000300.SH close-to-close forward return",
        "partition_count": partition_count,
        "method": {
            "version": METHOD_VERSION,
            "correlation": "Spearman rank correlation of daily market-level factor level with forward HS300 return",
            "regression": "OLS slope on standardized ranks with Newey-West HAC standard errors",
            "hac_lag": "equal to return horizon (5 or 20) for overlapping forward returns",
            "multiple_testing": "Benjamini-Hochberg FDR across factors within each horizon",
            "use": "market-regime conditioner research only; not a stock cross-sectional alpha",
        },
        "factor_count": len(results),
        "results": results,
        "ranked_factor_ids": [item["factor_id"] for item in ranked],
        "tier_counts": {
            tier: sum(1 for item in results.values() if item["tier"] == tier)
            for tier in sorted({item["tier"] for item in results.values()})
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_regime_scan_published"
        and record.input_fingerprints.get("research_id") == payload["research_id"]
        for record in ledger.records()
    ):
        ledger.append(
            "factor_regime_scan_published",
            payload={
                "catalog_snapshot_id": catalog.snapshot_id,
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "audit_id": args.audit_id,
                    "output_sha256": sha256_file(args.output),
                },
                "evidence_refs": [{
                    "kind": "factor_regime_scan",
                    "path": str(args.output.relative_to(DATA)),
                }],
                "metrics": {
                    "factor_count": len(results),
                    "tier_counts": payload["tier_counts"],
                },
                "verdict": "watch_only",
            },
        )
    print(json.dumps({
        "research_id": payload["research_id"],
        "factor_count": len(results),
        "tier_counts": payload["tier_counts"],
        "output": str(args.output),
        "top_10": [
            {
                "factor_id": item["factor_id"],
                "beta_5d": item["horizons"]["5"]["full"]["directional_beta"],
                "t_5d": item["horizons"]["5"]["full"]["nw_t"],
                "q_5d": item["horizons"]["5"]["q_value"],
                "recent_beta_5d": item["horizons"]["5"]["recent"]["directional_beta"],
                "beta_20d": item["horizons"]["20"]["full"]["directional_beta"],
                "tier": item["tier"],
            }
            for item in ranked[:10]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
