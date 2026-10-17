#!/usr/bin/env python3
"""R99: historical-membership parameter and 3x-cost stress tests.

This script does two separate things:

1. A finite macro-gate grid on discovery, validation and five recent fresh-start
   phases.  The grid is exploratory; it is not an out-of-sample proof.
2. A 3x-cost rerun of the strongest historical-membership candidates.

All runs use the daily historical HS300 membership snapshots.
"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import date
from itertools import product
from pathlib import Path

import polars as pl

from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
GRID_OUT = EVIDENCE / "r99_macro_gate_historical_parameter_grid.json"
COST_OUT = EVIDENCE / "r99_historical_3x_cost.json"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
DISCOVERY = (date(2021, 6, 1), date(2023, 12, 31))
VALIDATION = (date(2024, 1, 1), date(2026, 10, 16))
RECENT_PHASE_STARTS = [
    date(2025, 10, 14),
    date(2025, 12, 15),
    date(2026, 2, 13),
    date(2026, 4, 15),
    date(2026, 6, 15),
]
END = date(2026, 10, 16)
MACRO_STRATEGY = "ai_hs300_macro_gate_quality"
GRID = {
    "erp_min": [5.5, 6.0],
    "credit_max": [0.6, 0.8, 1.0],
    "term_min": [0.2, 0.3, 0.4],
}
COST_STRATEGIES = [
    "ai_hs300_macro_gate_quality",
    "ai_hs300_macro_gate_quality_erp60",
    "ai_hs300_fund_reduction_quality_industry_neutral",
    "ai_hs300_low_fund_crowding_quality_industry_neutral",
]


def _historical_membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP)
        .select([
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def _patched_membership(membership: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def _service() -> StrategyBacktestService:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(
        BacktestEngine(KlineRepository(DataStore(DATA))),
        engine,
    )


def _run(
    service: StrategyBacktestService,
    membership: pl.DataFrame,
    *,
    strategy_id: str,
    start: date,
    end: date,
    params: dict,
    cost_multiplier: float,
) -> dict:
    cfg = StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params=params,
        overrides={},
        matching="open_t+1",
        fees_pct=0.0002 * cost_multiplier,
        commission_pct=0.0002 * cost_multiplier,
        stamp_tax_pct=0.0005,
        slippage_bps=5.0 * cost_multiplier,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with _patched_membership(membership):
        result = service.run(cfg)
    return {
        "strategy_id": strategy_id,
        "start": str(start),
        "end": str(end),
        "params": params,
        "cost_multiplier": cost_multiplier,
        "error": result.error,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "stats": result.stats,
        "factor_attribution": result.factor_attribution,
    }


def _return_stats(records: list[dict]) -> dict:
    values = [
        float(record["stats"]["total_return"])
        for record in records
        if record.get("stats", {}).get("total_return") is not None
    ]
    if not values:
        return {"mean": None, "median": None, "positive": 0, "n": 0}
    ordered = sorted(values)
    return {
        "mean": round(sum(values) / len(values), 6),
        "median": round(ordered[len(ordered) // 2], 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "positive": sum(value > 0 for value in values),
        "n": len(values),
    }


def main() -> int:
    membership = _historical_membership()
    service = _service()

    grid_records = []
    for erp, credit, term in product(
        GRID["erp_min"], GRID["credit_max"], GRID["term_min"]
    ):
        params = {"erp_min": erp, "credit_max": credit, "term_min": term}
        discovery = _run(
            service,
            membership,
            strategy_id=MACRO_STRATEGY,
            start=DISCOVERY[0],
            end=DISCOVERY[1],
            params=params,
            cost_multiplier=2.0,
        )
        validation = _run(
            service,
            membership,
            strategy_id=MACRO_STRATEGY,
            start=VALIDATION[0],
            end=VALIDATION[1],
            params=params,
            cost_multiplier=2.0,
        )
        recent = [
            _run(
                service,
                membership,
                strategy_id=MACRO_STRATEGY,
                start=phase,
                end=END,
                params=params,
                cost_multiplier=2.0,
            )
            for phase in RECENT_PHASE_STARTS
        ]
        record = {
            "params": params,
            "discovery": discovery,
            "validation": validation,
            "recent_phases": recent,
            "recent_summary": _return_stats(recent),
        }
        grid_records.append(record)
        print(
            f"grid ERP={erp} credit={credit} term={term}: "
            f"disc={discovery['stats'].get('total_return')} "
            f"val={validation['stats'].get('total_return')} "
            f"recent_mean={record['recent_summary']['mean']} "
            f"recent_pos={record['recent_summary']['positive']}/5",
            flush=True,
        )

    grid_payload = {
        "purpose": "R99 finite macro-gate parameter grid with historical membership",
        "universe": "daily historical HS300 membership snapshots",
        "cost": "2x",
        "grid": GRID,
        "periods": {
            "discovery": [str(DISCOVERY[0]), str(DISCOVERY[1])],
            "validation": [str(VALIDATION[0]), str(VALIDATION[1])],
            "recent_phase_starts": [str(day) for day in RECENT_PHASE_STARTS],
        },
        "records": grid_records,
        "caveats": [
            "The grid is exploratory and multiple-testing biased.",
            "Recent fresh-start paths can share the same gated exposure period; they are not independent trials.",
            "A parameter is not promoted unless discovery, validation and recent behavior form a plateau.",
        ],
    }
    GRID_OUT.write_text(
        json.dumps(grid_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    cost_records = {}
    for strategy_id in COST_STRATEGIES:
        full = _run(
            service,
            membership,
            strategy_id=strategy_id,
            start=DISCOVERY[0],
            end=END,
            params={},
            cost_multiplier=3.0,
        )
        recent = [
            _run(
                service,
                membership,
                strategy_id=strategy_id,
                start=phase,
                end=END,
                params={},
                cost_multiplier=3.0,
            )
            for phase in RECENT_PHASE_STARTS
        ]
        cost_records[strategy_id] = {
            "full": full,
            "recent_phases": recent,
            "recent_summary": _return_stats(recent),
        }
        print(
            f"3x {strategy_id}: full={full['stats'].get('total_return')} "
            f"sharpe={full['stats'].get('sharpe')} "
            f"recent_mean={cost_records[strategy_id]['recent_summary']['mean']} "
            f"recent_pos={cost_records[strategy_id]['recent_summary']['positive']}/5",
            flush=True,
        )

    cost_payload = {
        "purpose": "R99 3x-cost stress with historical membership",
        "universe": "daily historical HS300 membership snapshots",
        "cost": {
            "fees_pct": 0.0006,
            "commission_pct": 0.0006,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 15.0,
        },
        "strategies": cost_records,
        "caveats": [
            "3x cost is a stress convention, not an estimate of actual execution cost.",
            "Recent fresh-start paths may share the same gated exposure period.",
            "No deployment approval is implied by this evidence.",
        ],
    }
    COST_OUT.write_text(
        json.dumps(cost_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {GRID_OUT} and {COST_OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
