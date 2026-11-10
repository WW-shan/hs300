#!/usr/bin/env python3
"""Parameter-family scan with train/test holdout and block-bootstrap confidence."""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import build_alt_factor_data as builder
import polars as pl

from app.backtest import stats_v2
from app.factor_research.fingerprints import sha256_file, sha256_json, utc_now
from app.factor_research.ledger import ResearchLedger
from app.factor_research.signal_scan import (
    block_bootstrap_mean_ci,
    calculate_rank_ic,
)
from app.services.industry_map import load_industry_l1
from app.strategy.scoring import neutralize_scoring_columns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_OUTPUT = DATA / "factor_research" / "research" / "alt_hs300_parameter_monte_carlo_2026-11-10.json"
DEFAULT_START = date(2021, 6, 1)
DEFAULT_END = date(2026, 11, 5)
TRAIN_END = date(2024, 12, 31)
TEST_START = date(2025, 1, 1)
RECENT_START = date(2025, 10, 14)
METHOD_VERSION = "parameter_mc_v1"
HORIZON = 5
BOOTSTRAP_BLOCK_SIZE = 20
BOOTSTRAP_RUNS = 1000

INDUSTRY_SIZE_SPEC = {
    "industry": "industry_l1",
    "styles": ["log_float_mv"],
    "exclude": [],
    "shrink": 1.0,
}


def _compute_idio(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    return builder._residual_volatility_frame(prices, market, window_size=value), "idio_vol_60d_low"


def _compute_cs_spread(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    del market
    return builder._corwin_schultz_spread_frame(prices, window_size=value), "cs_spread_60d"


def _compute_vol_of_vol(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    del market
    return builder._vol_of_vol_frame(prices, stability_window=value), "vol_of_vol_60d_low"


def _compute_down_beta(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    return builder._market_down_beta_frame(prices, market, window_size=value), "market_down_beta_60d_low"


def _compute_price_delay(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    return builder._price_delay_frame(prices, market, window_size=value), "price_delay_60d"


def _compute_trend_efficiency(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    del market
    return builder._trend_efficiency_frame(prices, window_size=value), "trend_efficiency_60d"


def _compute_risk_adjusted(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    del market
    return builder._risk_adjusted_momentum_frame(prices, window_size=value), "risk_adjusted_momentum_60d"


def _compute_beta_asymmetry(prices: pl.DataFrame, market: pl.DataFrame, value: int) -> tuple[pl.DataFrame, str]:
    return builder._beta_asymmetry_frame(prices, market, window_size=value), "beta_asymmetry_120d"


FAMILIES: dict[str, dict[str, Any]] = {
    "idio_vol": {
        "factor_id": "ext_alt_hs300_idio_vol_60d_low",
        "direction": "high",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_idio,
    },
    "cs_spread": {
        "factor_id": "ext_alt_hs300_cs_spread_60d",
        "direction": "low",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_cs_spread,
    },
    "vol_of_vol": {
        "factor_id": "ext_alt_hs300_vol_of_vol_60d_low",
        "direction": "high",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_vol_of_vol,
    },
    "market_down_beta": {
        "factor_id": "ext_alt_hs300_market_down_beta_60d_low",
        "direction": "high",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_down_beta,
    },
    "price_delay": {
        "factor_id": "ext_alt_hs300_price_delay_60d",
        "direction": "low",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_price_delay,
    },
    "trend_efficiency": {
        "factor_id": "ext_alt_hs300_trend_efficiency_60d",
        "direction": "high",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_trend_efficiency,
    },
    "risk_adjusted_momentum": {
        "factor_id": "ext_alt_hs300_risk_adjusted_momentum_60d",
        "direction": "high",
        "parameters": [20, 40, 60, 120],
        "current": 60,
        "compute": _compute_risk_adjusted,
    },
    "beta_asymmetry": {
        "factor_id": "ext_alt_hs300_beta_asymmetry_120d",
        "direction": "high",
        "parameters": [60, 120, 180, 240],
        "current": 120,
        "compute": _compute_beta_asymmetry,
    },
}


def _enriched_panel(start: date, end: date, symbols: list[str]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(
            pl.col("symbol").is_in(symbols)
            & (pl.col("date") >= start)
            & (pl.col("date") <= end)
        )
        .select(["symbol", "date", "open", "high", "low", "close", "volume", "amount", "turnover_rate"])
        .collect()
        .sort(["symbol", "date"])
    )


def _market_panel() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )


def _stats_for_period(ic: pl.DataFrame, start: date, end: date, sign: int) -> dict[str, Any]:
    values = ic.filter((pl.col("date") >= start) & (pl.col("date") <= end))["ic"].to_numpy()
    if values.size == 0:
        return {"n_dates": 0, "directional_ic": None, "nw_t": None, "p_value": None}
    nw = stats_v2.newey_west_t(values, lag=HORIZON)
    return {
        "n_dates": int(values.size),
        "directional_ic": round(float(values.mean()) * sign, 6),
        "nw_t": round(float(nw[0]), 6) if nw is not None else None,
        "p_value": round(float(stats_v2.normal_two_sided_p(nw[0])), 8) if nw is not None else None,
    }


def _bootstrap(ic: pl.DataFrame, start: date, end: date, sign: int) -> dict[str, Any]:
    values = ic.filter((pl.col("date") >= start) & (pl.col("date") <= end))["ic"].to_numpy() * sign
    return block_bootstrap_mean_ci(
        values,
        block_size=BOOTSTRAP_BLOCK_SIZE,
        n_bootstrap=BOOTSTRAP_RUNS,
        seed=20261110 + int(start.strftime("%Y%m%d")),
    )


def _plateau(trials: list[dict[str, Any]]) -> None:
    ordered = sorted(trials, key=lambda item: item["parameter"])
    for index, trial in enumerate(ordered):
        neighbors = []
        if index > 0:
            neighbors.append(ordered[index - 1])
        if index + 1 < len(ordered):
            neighbors.append(ordered[index + 1])
        robust = [
            item for item in [trial, *neighbors]
            if (item["train"]["directional_ic"] or 0.0) > 0
            and (item["test"]["directional_ic"] or 0.0) > 0
            and (item["full"]["directional_ic"] or 0.0) > 0
        ]
        trial["neighbor_count"] = len(neighbors)
        trial["robust_plateau"] = len(robust) == len(neighbors) + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=str(DEFAULT_START))
    parser.add_argument("--end", default=str(DEFAULT_END))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    from app.hs300.current import current_symbols

    symbols = current_symbols()
    load_start = date(2019, 1, 1)
    prices = _enriched_panel(load_start, end, symbols)
    market = _market_panel()
    prices = prices.with_columns(
        (pl.col("close").shift(-HORIZON).over("symbol") / pl.col("close") - 1.0).alias("forward_return_5d"),
        pl.when((pl.col("turnover_rate") > 0) & (pl.col("volume") > 0))
        .then((pl.col("close") * pl.col("volume") / pl.col("turnover_rate")).log())
        .otherwise(None)
        .alias("log_float_mv"),
    )
    industry = load_industry_l1(DATA).select(["symbol", "industry_l1"]).unique(subset=["symbol"], keep="last")
    prices = prices.join(industry, on="symbol", how="left")

    family_results: dict[str, Any] = {}
    all_trials: list[dict[str, Any]] = []
    for family, spec in FAMILIES.items():
        sign = -1 if spec["direction"] == "low" else 1
        trials: list[dict[str, Any]] = []
        compute: Callable[[pl.DataFrame, pl.DataFrame, int], tuple[pl.DataFrame, str]] = spec["compute"]
        for parameter in spec["parameters"]:
            raw, output_column = compute(prices, market, parameter)
            factor_frame = raw.select([
                "symbol",
                "date",
                pl.col(output_column).alias("factor_value"),
            ])
            joined = prices.select([
                "symbol", "date", "forward_return_5d", "industry_l1", "log_float_mv"
            ]).join(factor_frame, on=["symbol", "date"], how="inner")
            joined = joined.filter(pl.col("forward_return_5d").is_not_null())
            raw_ic = calculate_rank_ic(joined, "factor_value", "forward_return_5d", minimum=30)
            neutral_input = joined.select([
                "date", "symbol", "forward_return_5d", "factor_value", "industry_l1", "log_float_mv"
            ])
            neutralized = neutralize_scoring_columns(
                neutral_input, ["factor_value"], INDUSTRY_SIZE_SPEC
            )
            neutral_ic = calculate_rank_ic(neutralized, "factor_value", "forward_return_5d", minimum=30)
            trial = {
                "parameter": parameter,
                "is_current": parameter == spec["current"],
                "full": _stats_for_period(raw_ic, start, end, sign),
                "train": _stats_for_period(raw_ic, start, TRAIN_END, sign),
                "test": _stats_for_period(raw_ic, TEST_START, end, sign),
                "recent": _stats_for_period(raw_ic, RECENT_START, end, sign),
                "bootstrap_full": _bootstrap(raw_ic, start, end, sign),
                "industry_size_neutral": {
                    "full": _stats_for_period(neutral_ic, start, end, sign),
                    "train": _stats_for_period(neutral_ic, start, TRAIN_END, sign),
                    "test": _stats_for_period(neutral_ic, TEST_START, end, sign),
                    "recent": _stats_for_period(neutral_ic, RECENT_START, end, sign),
                    "bootstrap_full": _bootstrap(neutral_ic, start, end, sign),
                },
                "q_value": None,
                "neutral_q_value": None,
            }
            trials.append(trial)
        _plateau(trials)
        raw_q = stats_v2.bh_fdr_qvalues([item["full"]["p_value"] for item in trials])
        neutral_q = stats_v2.bh_fdr_qvalues([
            item["industry_size_neutral"]["full"]["p_value"] for item in trials
        ])
        for item, q_value, neutral_value in zip(trials, raw_q, neutral_q, strict=True):
            item["q_value"] = round(float(q_value), 8) if q_value is not None else None
            item["neutral_q_value"] = round(float(neutral_value), 8) if neutral_value is not None else None
        stable = [
            item for item in trials
            if item["robust_plateau"]
            and (item["full"]["directional_ic"] or 0.0) > 0
            and (item["train"]["directional_ic"] or 0.0) > 0
            and (item["test"]["directional_ic"] or 0.0) > 0
            and (item["bootstrap_full"]["ci_low"] or -1.0) > 0
        ]
        recommended = max(
            stable,
            key=lambda item: min(
                item["train"]["directional_ic"] or 0.0,
                item["test"]["directional_ic"] or 0.0,
            ),
        )["parameter"] if stable else None
        current = next(item for item in trials if item["is_current"])
        family_results[family] = {
            "factor_id": spec["factor_id"],
            "direction": spec["direction"],
            "current_parameter": spec["current"],
            "recommended_parameter": recommended,
            "current_trial": current,
            "trials": trials,
        }
        all_trials.extend(trials)

    payload = {
        "schema_version": 1,
        "research_id": sha256_json({
            "method_version": METHOD_VERSION,
            "start": str(start),
            "end": str(end),
            "families": list(FAMILIES),
            "parameters": {key: value["parameters"] for key, value in FAMILIES.items()},
        }),
        "generated_at": utc_now(),
        "method_version": METHOD_VERSION,
        "period": {
            "start": str(start),
            "end": str(end),
            "train_end": str(TRAIN_END),
            "test_start": str(TEST_START),
            "recent_start": str(RECENT_START),
        },
        "universe": {
            "name": "hs300_current",
            "symbol_count": len(symbols),
            "survivorship_bias": True,
        },
        "return_horizon_days": HORIZON,
        "method": {
            "parameter_scan": "neighboring windows, not a claim that correlated grid points are independent discoveries",
            "train_test": "2021-06-01..2024-12-31 train; 2025-01-01..end test",
            "monte_carlo": "moving-block bootstrap of daily Rank IC with 20-day blocks",
            "bootstrap_runs": BOOTSTRAP_RUNS,
            "multiple_testing": "BH-FDR across each family's parameter grid",
            "neutralization": "industry + log_float_mv residualization for robustness check",
        },
        "family_count": len(family_results),
        "trial_count": len(all_trials),
        "recommended_parameter_count": sum(
            1 for item in family_results.values() if item["recommended_parameter"] is not None
        ),
        "families": family_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = ResearchLedger(DATA)
    if not any(
        record.operation == "factor_parameter_mc_published"
        and record.input_fingerprints.get("research_id") == payload["research_id"]
        for record in ledger.records()
    ):
        ledger.append(
            "factor_parameter_mc_published",
            payload={
                "input_fingerprints": {
                    "research_id": payload["research_id"],
                    "output_sha256": sha256_file(args.output),
                },
                "evidence_refs": [{
                    "kind": "factor_parameter_mc",
                    "path": str(args.output.relative_to(DATA)),
                }],
                "metrics": {
                    "family_count": len(family_results),
                    "trial_count": len(all_trials),
                    "recommended_parameter_count": payload["recommended_parameter_count"],
                },
                "verdict": "watch_only",
            },
        )
    print(json.dumps({
        "research_id": payload["research_id"],
        "family_count": len(family_results),
        "trial_count": len(all_trials),
        "output": str(args.output),
        "families": {
            family: {
                "current": item["current_parameter"],
                "recommended": item["recommended_parameter"],
                "current_full_ic": item["current_trial"]["full"]["directional_ic"],
                "current_test_ic": item["current_trial"]["test"]["directional_ic"],
                "current_bootstrap_ci": [
                    item["current_trial"]["bootstrap_full"]["ci_low"],
                    item["current_trial"]["bootstrap_full"]["ci_high"],
                ],
            }
            for family, item in family_results.items()
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
