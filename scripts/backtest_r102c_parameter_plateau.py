#!/usr/bin/env python3
"""R102c: pre-registered holding/position-size plateau for R101 baseline."""
from __future__ import annotations

import json
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
    / "evidence" / "r102c_parameter_plateau.json"
)
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
STRATEGY_ID = "ai_hs300_industry_relative_strength_quality"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
HOLDS = [20, 35, 50]
POSITIONS = [10, 15, 20]


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
    return StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(DATA))), engine)


def _run(
    service: StrategyBacktestService,
    membership: pl.DataFrame,
    *,
    start: date,
    hold: int,
    positions: int,
) -> dict:
    cfg = StrategyBacktestConfig(
        strategy_id=STRATEGY_ID,
        symbols=None,
        start=start,
        end=FULL_END,
        params={},
        overrides={"max_hold_days": hold},
        matching="open_t+1",
        fees_pct=0.0004,
        commission_pct=0.0004,
        stamp_tax_pct=0.0005,
        slippage_bps=10.0,
        max_positions=positions,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    with _patched(membership):
        result = service.run(cfg)
    return {
        "start": str(start),
        "end": str(FULL_END),
        "hold": hold,
        "positions": positions,
        "error": result.error,
        "stats": result.stats,
    }


def _summary(records: list[dict]) -> dict:
    returns = [float(r["stats"]["total_return"]) for r in records]
    sharpes = [float(r["stats"]["sharpe"]) for r in records]
    return {
        "return_mean": round(sum(returns) / len(returns), 6),
        "return_median": round(sorted(returns)[len(returns) // 2], 6),
        "return_min": round(min(returns), 6),
        "return_max": round(max(returns), 6),
        "positive": sum(value > 0 for value in returns),
        "n": len(returns),
        "sharpe_mean": round(sum(sharpes) / len(sharpes), 6),
    }


def main() -> int:
    membership = _membership()
    service = _service()
    output = {}
    for hold in HOLDS:
        for positions in POSITIONS:
            key = f"hold{hold}_pos{positions}"
            full = _run(
                service, membership, start=FULL_START, hold=hold, positions=positions,
            )
            recent = [
                _run(service, membership, start=day, hold=hold, positions=positions)
                for day in RECENT
            ]
            output[key] = {
                "hold": hold,
                "positions": positions,
                "full": full,
                "recent_results": recent,
                "recent_summary": _summary(recent),
            }
            print(
                f"{key}: full={full['stats'].get('total_return')} "
                f"sharpe={full['stats'].get('sharpe')} recent={output[key]['recent_summary']}",
                flush=True,
            )
    payload = {
        "purpose": "R102c holding/position-size parameter plateau",
        "strategy_id": STRATEGY_ID,
        "universe": "daily historical HS300 membership",
        "cost": {"fees_pct": 0.0004, "commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
        "grid": {"max_hold_days": HOLDS, "max_positions": POSITIONS},
        "periods": {"full": [str(FULL_START), str(FULL_END)], "recent_starts": [str(d) for d in RECENT]},
        "variants": output,
        "caveats": ["Pre-registered small grid; select a plateau, not a single peak."],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
