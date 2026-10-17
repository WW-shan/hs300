#!/usr/bin/env python3
"""R100b: 3x-cost check for the neutral event/behaviour candidates."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import date
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
OUT = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank"
    / "evidence" / "r100b_neutral_3x_cost.json"
)
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
END = date(2026, 10, 16)
PHASES = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
STRATEGIES = [
    "ai_hs300_pledge_quality",
    "ai_hs300_block_trade_quality",
    "ai_hs300_event_combo",
]
OVERRIDES = {"neutralize": {"industry": "industry_l1"}}


def _membership() -> pl.DataFrame:
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
def _patched(membership: pl.DataFrame):
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
        BacktestEngine(KlineRepository(DataStore(DATA))), engine
    )


def _run(service: StrategyBacktestService, membership: pl.DataFrame, sid: str, start: date) -> dict:
    cfg = StrategyBacktestConfig(
        strategy_id=sid,
        symbols=None,
        start=start,
        end=END,
        params={},
        overrides=OVERRIDES,
        matching="open_t+1",
        fees_pct=0.0006,
        commission_pct=0.0006,
        stamp_tax_pct=0.0005,
        slippage_bps=15.0,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with _patched(membership):
        result = service.run(cfg)
    print(
        f"{sid} {start}: err={result.error} ret={result.stats.get('total_return')} "
        f"sharpe={result.stats.get('sharpe')} mdd={result.stats.get('max_drawdown')}",
        flush=True,
    )
    return {
        "strategy_id": sid,
        "start": str(start),
        "end": str(END),
        "overrides": OVERRIDES,
        "cost_multiplier": 3.0,
        "error": result.error,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "stats": result.stats,
        "factor_attribution": result.factor_attribution,
    }


def _summary(records: list[dict]) -> dict:
    def stat(key: str) -> dict | None:
        values = [
            float(record["stats"][key])
            for record in records
            if record.get("stats", {}).get(key) is not None
        ]
        if not values:
            return None
        ordered = sorted(values)
        return {
            "mean": round(sum(values) / len(values), 6),
            "median": round(ordered[len(ordered) // 2], 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "positive": sum(value > 0 for value in values),
            "n": len(values),
        }
    return {
        "total_return": stat("total_return"),
        "sharpe": stat("sharpe"),
        "max_drawdown": stat("max_drawdown"),
    }


def main() -> int:
    membership = _membership()
    service = _service()
    output = {}
    for sid in STRATEGIES:
        full = _run(service, membership, sid, date(2021, 6, 1))
        phases = [_run(service, membership, sid, day) for day in PHASES]
        recent = [_run(service, membership, sid, day) for day in RECENT]
        output[sid] = {
            "full": full,
            "phase_results": phases,
            "phase_summary": _summary(phases),
            "recent_results": recent,
            "recent_summary": _summary(recent),
        }
    payload = {
        "purpose": "R100b 3x-cost neutral event strategies",
        "universe": "daily historical HS300 membership",
        "cost": {
            "fees_pct": 0.0006,
            "commission_pct": 0.0006,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 15.0,
        },
        "overrides": OVERRIDES,
        "strategies": output,
        "caveats": [
            "3x cost is a stress convention, not an execution estimate.",
            "Pledge coverage is limited to stocks with vendor pledge snapshots.",
            "Block-trade data starts in 2023-09; earlier phases are empty by construction.",
            "No deployment approval is implied.",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
