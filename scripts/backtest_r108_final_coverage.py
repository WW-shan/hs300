#!/usr/bin/env python3
"""R108: final uncovered-factor composite backtests."""
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
    / "evidence" / "r108_final_coverage_backtests.json"
)
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
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
    "ai_hs300_medium_term_trend_quality",
    "ai_hs300_short_term_risk_reversal_quality",
]
FACTORS = [
    "cf_medium_term_trend",
    "cf_short_term_risk_reversal",
    "momentum_30d",
    "ma5_bias",
    "ma30_bias",
    "ema20_bias",
    "ema30_bias",
    "ema60_bias",
    "macd_dif_pct",
    "rsi_6",
    "rsi_24",
    "kdj_d",
    "ema5_bias",
    "atr_14",
    "atr_pct",
    "macd_dea_pct",
    "uf_quality_value_defensive",
    "uf_cash_quality",
]


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
    strategy_id: str,
    start: date,
    cost_multiplier: float = 2.0,
    max_positions: int = 10,
) -> dict:
    cfg = StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=FULL_END,
        params={},
        overrides={},
        matching="open_t+1",
        fees_pct=0.0002 * cost_multiplier,
        commission_pct=0.0002 * cost_multiplier,
        stamp_tax_pct=0.0005,
        slippage_bps=5.0 * cost_multiplier,
        max_positions=max_positions,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with _patched(membership):
        result = service.run(cfg)
    print(
        f"{strategy_id} {start}: err={result.error} ret={result.stats.get('total_return')} "
        f"sharpe={result.stats.get('sharpe')} mdd={result.stats.get('max_drawdown')}",
        flush=True,
    )
    return {
        "strategy_id": strategy_id,
        "start": str(start),
        "end": str(FULL_END),
        "cost_multiplier": cost_multiplier,
        "max_positions": max_positions,
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
        "n_trades": stat("n_trades"),
    }


def _factor_metadata() -> dict:
    from app.factors.registry import get_factor

    out = {}
    for factor_id in FACTORS:
        spec = get_factor(factor_id)
        out[factor_id] = {
            "registered": spec is not None,
            "kind": spec.kind if spec else None,
            "components": list(spec.components) if spec and spec.components else [],
            "dependencies": sorted(spec.dependencies) if spec else [],
        }
    return out


def main() -> int:
    membership = _membership()
    service = _service()
    output = {}
    for sid in STRATEGIES:
        full = _run(service, membership, strategy_id=sid, start=FULL_START)
        phases = [
            _run(service, membership, strategy_id=sid, start=day)
            for day in PHASES
        ]
        recent = [
            _run(service, membership, strategy_id=sid, start=day)
            for day in RECENT
        ]
        stress_full = _run(
            service, membership, strategy_id=sid, start=FULL_START, cost_multiplier=3.0,
        )
        stress_recent = [
            _run(service, membership, strategy_id=sid, start=day, cost_multiplier=3.0)
            for day in RECENT
        ]
        output[sid] = {
            "full": full,
            "phase_results": phases,
            "phase_summary": _summary(phases),
            "recent_results": recent,
            "recent_summary": _summary(recent),
            "cost3x": {
                "full": stress_full,
                "recent_results": stress_recent,
                "recent_summary": _summary(stress_recent),
            },
        }
    payload = {
        "purpose": "R108 final uncovered-factor composite strategies",
        "engine": "StrategyBacktestService / project T+1 execution path",
        "universe": "daily historical HS300 membership",
        "cost": {
            "2x": {"fees_pct": 0.0004, "commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
            "3x": {"fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.0005, "slippage_bps": 15.0},
        },
        "periods": {
            "full": [str(FULL_START), str(FULL_END)],
            "phase_starts": [str(day) for day in PHASES],
            "recent_starts": [str(day) for day in RECENT],
        },
        "strategies": STRATEGIES,
        "factors": _factor_metadata(),
        "results": output,
        "caveats": [
            "This batch covers the final 14 factors left by the R107 audit.",
            "All factors use only T-day or earlier data and T+1 execution.",
            "Experimental strategies are not deployment-approved.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
