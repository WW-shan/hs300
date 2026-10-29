#!/usr/bin/env python3
"""Audit point-in-time dating for the R275 macro gate inputs."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RESULT = EVIDENCE / "r275_single_gate_ablation_v3d.json"
PREREG = EVIDENCE / "r275_preregistration_v3d.json"
OUT = EVIDENCE / "r276_macro_pit_audit_v5.json"
FACTORS = [
    "equity_risk_premium",
    "credit_spread_5y_low",
    "term_spread_10y_2y",
]
START = "2021-06-01"
END = "2026-10-16"
CACHE_R275 = DATA / "research/r275_cache_v3d"
CACHE_R266 = DATA / "research/r266_cache"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _annual_profile(cache: Path, prefix: str) -> dict[str, dict]:
    curve_path = cache / f"{prefix}_3x_{START}_{END}.parquet"
    trades_path = cache / f"{prefix}_3x_{START}_{END}.trades.json"
    curve = pl.read_parquet(curve_path).sort("date")
    trades = json.loads(trades_path.read_text(encoding="utf-8"))
    result = {}
    for year in range(2021, 2027):
        year_end = curve.filter(pl.col("date").dt.year() == year)
        if year_end.height == 0:
            continue
        year_start = curve.filter(pl.col("date").dt.year() < year)
        base = float(year_start["value"][-1]) if year_start.height else float(year_end["value"][0])
        ending = float(year_end["value"][-1])
        year_trades = [trade for trade in trades if int(trade["entry_date"][:4]) == year]
        result[str(year)] = {
            "curve_return": round(ending / base - 1.0, 6),
            "entry_trades": len(year_trades),
            "realized_net_pnl": round(sum(float(trade["pnl_amount"]) for trade in year_trades), 2),
            "positive_pnl_trades": sum(float(trade["pnl_amount"]) > 0 for trade in year_trades),
        }
    return {"curve_sha256": _sha(curve_path), "trades_sha256": _sha(trades_path), "years": result}


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R276 PIT audit exists; refusing overwrite")
    paths = sorted((DATA / "ext_data/alt_hs300/timeseries").glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("macro time-series partitions unavailable")
    # The partition date is encoded in the folder name, not stored in the parquet rows.
    frames = [
        pl.read_parquet(path).select("symbol", *FACTORS).with_columns(
            pl.lit(path.parent.name.removeprefix("date=")).str.to_date().alias("date")
        )
        for path in paths
    ]
    panel = pl.concat(frames, how="vertical_relaxed").select("date", "symbol", *FACTORS).sort(["symbol", "date"])
    if panel.height == 0:
        raise RuntimeError("macro time-series panel is empty")
    evidence = {}
    for factor in FACTORS:
        values = panel.filter(pl.col(factor).is_not_null()).select("date", "symbol", factor).sort(["date", "symbol"])
        by_date = values.group_by("date").agg(
            pl.len().alias("rows"),
            pl.col("symbol").n_unique().alias("symbols"),
            pl.col(factor).n_unique().alias("distinct_values"),
            pl.col(factor).first().alias("value"),
        ).sort("date").with_columns(pl.col("value").diff().alias("change"))
        if int(by_date["distinct_values"].max()) != 1:
            raise RuntimeError(f"{factor} is not a consistent market-level value per date")
        changed_dates = by_date.filter(pl.col("change").is_not_null() & (pl.col("change") != 0))
        change_gaps = changed_dates["date"].diff().dt.total_days().drop_nulls().to_list()
        evidence[factor] = {
            "non_null_rows": values.height,
            "symbols": values["symbol"].n_unique(),
            "first_value_date": str(values["date"].min()),
            "last_value_date": str(values["date"].max()),
            "value_dates": by_date.height,
            "daily_symbol_count_median": int(by_date["symbols"].median()),
            "max_distinct_values_per_date": int(by_date["distinct_values"].max()),
            "days_with_any_value_change": changed_dates.height,
            "days_with_unchanged_value": by_date.height - changed_dates.height - 1,
            "last_change_date": str(changed_dates["date"].max()) if changed_dates.height else None,
            "change_gap_days_frequency": sorted(Counter(change_gaps).items()),
            "sample_transitions": changed_dates.select("date", "change").tail(12).to_dicts(),
        }
    unique_dates = panel["date"].n_unique()
    source_files = {path.relative_to(ROOT).as_posix(): _sha(path) for path in paths}
    raw_inputs = [
        DATA / "research/macro/bond_10y.parquet",
        DATA / "research/r93_erp/hs300_pe.parquet",
        *sorted((DATA / "research/r92_credit_spread").glob("*.parquet")),
    ]
    raw_source_files = {path.relative_to(ROOT).as_posix(): _sha(path) for path in raw_inputs if path.exists()}
    annual_performance = {
        "r275_term": _annual_profile(CACHE_R275, "ai_hs300_macro_gate_quality_r275_remove_term"),
        "r266_control": _annual_profile(CACHE_R266, "ai_hs300_macro_gate_quality"),
    }
    payload = {
        "round": "R276",
        "classification": "forensic_data_timing_audit_post_hoc_not_a_new_strategy_test",
        "purpose": "Determine whether the R275 term-spread ablation can be interpreted as point-in-time macro alpha, given daily repeated values and unknown source publication timestamps.",
        "r275_result_sha256": _sha(RESULT),
        "r275_preregistration_sha256": _sha(PREREG),
        "r275_term_3x_manifest_sha256": _sha(CACHE_R275 / f"ai_hs300_macro_gate_quality_r275_remove_term_3x_{START}_{END}.manifest.json"),
        "r266_control_evidence_sha256": _sha(EVIDENCE / "r266_a_leg_validation.json"),
        "audit_runner_sha256": _sha(Path(__file__).resolve()),
        "macro_config_sha256": _sha(DATA / "ext_data/alt_hs300/config.json"),
        "source_data_files": source_files,
        "raw_source_files": raw_source_files,
        "annual_performance_post_hoc": annual_performance,
        "panel": {"rows": panel.height, "unique_dates": unique_dates, "first_date": str(panel["date"].min()), "last_partition_date": str(panel["date"].max()), "symbols": panel["symbol"].n_unique()},
        "factor_timing": evidence,
        "limitations": [
            "Stored date is the dataset partition/as-of date; provider collection time and original publication timestamp are not recorded in this schema.",
            "Repeated values and change dates cannot prove when underlying macro observations became publicly available.",
            "The market data ends 2026-10-16 while the current macro partition inventory may contain later rows; historical backtests were capped at 2026-10-16 and this audit does not use later values to change returns.",
            "This is a data lineage audit only. It cannot certify PIT correctness or establish prospective alpha.",
        ],
        "decision": "point_in_time_not_proven; keep the term-spread ablation research-only and do not promote on the historical backtest alone",
        "production_status": "pause_production",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    digest = _sha(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"audit": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "panel": payload["panel"], "factor_timing": {key: {field: item[field] for field in ("first_value_date", "last_value_date", "value_dates", "max_distinct_values_per_date", "days_with_any_value_change", "days_with_unchanged_value", "last_change_date")} for key, item in evidence.items()}, "annual_performance_post_hoc": annual_performance, "decision": payload["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
