#!/usr/bin/env python3
"""Audit the R261 factor build before reading any strategy returns."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
)
OUT = EVIDENCE / "r261_factor_build.json"
BACKTEST = ROOT / "scripts" / "backtest_r261_quality_low_lottery.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    bt = _load(BACKTEST, "r261_factor_audit")
    builder = _load(bt.R261_BUILDER, "r261_factor_audit_builder")
    base = _load(bt.BASE_BUILDER, "r261_factor_audit_base")
    r166 = _load(bt.R166_SCRIPT, "r261_factor_audit_r166")
    r221 = _load(bt.R221_SCRIPT, "r261_factor_audit_r221")
    r229 = _load(bt.R229_SCRIPT, "r261_factor_audit_r229")

    calendar = r229._benchmark_dates(bt.START, bt.END)
    all_members = r221._membership_with_industry()
    membership = r221._nonfin_membership(all_members)
    universe = r221._universe_audit(all_members)

    factor_all = bt._factor_rows()
    r257_all = bt._r257_rows()
    low_lottery_all = builder._low_lottery_frame()
    r249_all = bt._r249_rows()

    coverage = bt._coverage(membership, factor_all)
    pit_audit = bt._pit_audit(factor_all, builder, base)
    increment = bt._increment_audit(
        factor_all, r257_all, low_lottery_all, membership, r166
    )
    independent_risk = bt._independent_risk_audit(
        low_lottery_all, r249_all, membership, calendar, r166
    )

    factor_paths = sorted(
        (DATA / "ext_data" / bt.FACTOR_CONFIG / "timeseries").glob(
            "date=*/part.parquet"
        )
    )
    valid = factor_all.filter(pl.col(bt.FACTOR_COLUMN).is_not_null())
    checks = {
        "full_coverage": coverage["full"]["coverage"] >= 0.99,
        "recent_coverage": coverage["recent_12m"]["coverage"] >= 0.99,
        "discovery_coverage": coverage["discovery"]["coverage"] >= 0.99,
        "validation_coverage": coverage["validation"]["coverage"] >= 0.99,
        "pit": pit_audit["all_checks_pass"],
        "first_valid_by_start": coverage["first_valid_date"] <= str(bt.START),
        "increment": increment["all_checks_pass"],
        "independent_risk": independent_risk["all_checks_pass"],
    }
    payload = {
        "round": bt.ROUND,
        "status": "factor_build_and_pit_audit_completed_before_strategy_returns",
        "factor_id": bt.FACTOR_ID,
        "config_id": bt.FACTOR_CONFIG,
        "field": bt.FACTOR_COLUMN,
        "formula": (
            "quality_low_lottery_min_rank = "
            "min(rank_pct(quality_relative_strength_min_rank), "
            "rank_pct(-max_ret_20d)) within historical nonfinancial HS300 members"
        ),
        "components": list(bt.COMPONENT_COLUMNS),
        "lottery_window": bt.LOTTERY_WINDOW,
        "partitions": len(factor_paths),
        "factor_rows": factor_all.height,
        "valid_rows": valid.height,
        "valid_symbols": valid["symbol"].n_unique(),
        "valid_dates": valid["date"].n_unique(),
        "first_valid_date": str(valid["date"].min()),
        "last_date": str(factor_all["date"].max()),
        "factor_min": float(valid[bt.FACTOR_COLUMN].min()),
        "factor_median": float(valid[bt.FACTOR_COLUMN].median()),
        "factor_max": float(valid[bt.FACTOR_COLUMN].max()),
        "universe": universe,
        "coverage": coverage,
        "pit_audit": pit_audit,
        "increment_audit": increment,
        "independent_risk_audit": independent_risk,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "selection_note": (
            "This audit reads no R261 strategy returns. Factor construction, coverage, "
            "PIT reproducibility and independence must pass before panel registration "
            "and strategy backtesting."
        ),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "factor_id": payload["factor_id"],
                "partitions": payload["partitions"],
                "factor_rows": payload["factor_rows"],
                "valid_rows": payload["valid_rows"],
                "coverage": coverage,
                "increment": {
                    "months": increment["months"],
                    "mean_abs_correlation": increment["mean_abs_correlation"],
                    "max_correlation": increment["max_correlation"],
                    "mean_reduction_vs_r257_rank": increment[
                        "mean_reduction_vs_r257_rank"
                    ],
                    "share_reduced_by_at_least_010": increment[
                        "share_reduced_by_at_least_010"
                    ],
                    "all_checks_pass": increment["all_checks_pass"],
                },
                "independent_risk": {
                    "months": independent_risk["months"],
                    "mean_abs_correlation": independent_risk[
                        "mean_abs_correlation"
                    ],
                    "max_abs_correlation": independent_risk[
                        "max_abs_correlation"
                    ],
                    "all_checks_pass": independent_risk["all_checks_pass"],
                },
                "pit": pit_audit["all_checks_pass"],
                "checks": checks,
                "all_checks_pass": payload["all_checks_pass"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print("saved", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
