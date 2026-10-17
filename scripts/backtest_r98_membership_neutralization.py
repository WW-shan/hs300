#!/usr/bin/env python3
"""R98: historical HS300 membership stress test + style neutralization.

The earlier R96/R97 backtests used the current HS300 membership for the whole
history.  This script reruns the strongest experimental strategies with the
daily historical membership snapshots and compares four variants:

- current membership, strategy defaults
- historical membership, strategy defaults
- historical membership + industry neutralization
- historical membership + industry + log-float-market-cap neutralization

Only evidence is written; no strategy is promoted by this script.
"""
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
    DATA
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
    / "r98_historical_membership_neutralization.json"
)
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
PHASE_STARTS = [
    date(2021, 6, 1),
    date(2022, 1, 4),
    date(2022, 7, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
    date(2024, 7, 1),
    date(2025, 1, 2),
    date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT_PHASE_STARTS = [
    date(2025, 10, 14),
    date(2025, 12, 15),
    date(2026, 2, 13),
    date(2026, 4, 15),
    date(2026, 6, 15),
]
STRATEGIES = [
    "ai_hs300_macro_gate_quality",
    "ai_hs300_macro_gate_quality_erp60",
    "ai_hs300_macro_gate_quality_industry_neutral",
    "ai_hs300_holder_flow_quality",
    "ai_hs300_insider_net_quality",
    "ai_hs300_dividend_yield_quality",
    "ai_hs300_low_fund_crowding_quality",
    "ai_hs300_fund_reduction_quality",
]
PHASE_STRATEGIES = [
    "ai_hs300_macro_gate_quality",
    "ai_hs300_macro_gate_quality_erp60",
    "ai_hs300_macro_gate_quality_industry_neutral",
    "ai_hs300_insider_net_quality",
    "ai_hs300_low_fund_crowding_quality",
    "ai_hs300_fund_reduction_quality",
]
VARIANTS = {
    "current_default": {"historical": False, "overrides": {}},
    "historical_default": {"historical": True, "overrides": {}},
    "historical_industry": {
        "historical": True,
        "overrides": {"neutralize": {"industry": "industry_l1"}},
    },
    "historical_industry_size": {
        "historical": True,
        "overrides": {
            "neutralize": {
                "industry": "industry_l1",
                "styles": ["log_float_mv"],
            }
        },
    },
}


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


def _config(
    strategy_id: str,
    start: date,
    end: date,
    *,
    overrides: dict,
    max_positions: int = 10,
) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides=overrides,
        matching="open_t+1",
        fees_pct=0.0004,
        commission_pct=0.0004,
        stamp_tax_pct=0.0005,
        slippage_bps=10.0,
        max_positions=max_positions,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )


def _curve_return(curve: list[dict]) -> float | None:
    if not curve:
        return None
    first = float(curve[0].get("value", 0.0))
    last = float(curve[-1].get("value", 0.0))
    return last / first - 1.0 if first else None


def _run(
    service: StrategyBacktestService,
    membership: pl.DataFrame,
    *,
    strategy_id: str,
    variant: str,
    start: date,
    end: date,
    max_positions: int = 10,
) -> dict:
    spec = VARIANTS[variant]
    cfg = _config(
        strategy_id,
        start,
        end,
        overrides=spec["overrides"],
        max_positions=max_positions,
    )
    started = time.perf_counter()
    if spec["historical"]:
        with _patched_membership(membership):
            result = service.run(cfg)
    else:
        result = service.run(cfg)
    elapsed = round(time.perf_counter() - started, 3)
    record = {
        "strategy_id": strategy_id,
        "variant": variant,
        "start": str(start),
        "end": str(end),
        "max_positions": max_positions,
        "overrides": spec["overrides"],
        "error": result.error,
        "elapsed_seconds": elapsed,
        "stats": result.stats,
        "benchmark_return": _curve_return(result.benchmark_curve),
        "factor_attribution": result.factor_attribution,
    }
    print(
        f"{strategy_id} {variant} {start} p{max_positions}: "
        f"err={result.error} ret={result.stats.get('total_return')} "
        f"sharpe={result.stats.get('sharpe')} mdd={result.stats.get('max_drawdown')} "
        f"trades={result.stats.get('n_trades')}",
        flush=True,
    )
    return record


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
        "annual_return": stat("annual_return"),
        "sharpe": stat("sharpe"),
        "max_drawdown": stat("max_drawdown"),
        "n_trades": stat("n_trades"),
    }


def main() -> int:
    membership = _historical_membership()
    service = _service()
    full: dict[str, dict] = {}
    phase_results: dict[str, dict[str, list[dict]]] = {}
    recent_results: dict[str, dict[str, list[dict]]] = {}

    for strategy_id in STRATEGIES:
        for variant in VARIANTS:
            full[f"{strategy_id}:{variant}:p10"] = _run(
                service,
                membership,
                strategy_id=strategy_id,
                variant=variant,
                start=FULL_START,
                end=FULL_END,
                max_positions=10,
            )
            full[f"{strategy_id}:{variant}:p20"] = _run(
                service,
                membership,
                strategy_id=strategy_id,
                variant=variant,
                start=FULL_START,
                end=FULL_END,
                max_positions=20,
            )

    for strategy_id in PHASE_STRATEGIES:
        phase_results[strategy_id] = {}
        recent_results[strategy_id] = {}
        for variant in ("historical_default", "historical_industry"):
            phase_results[strategy_id][variant] = [
                _run(
                    service,
                    membership,
                    strategy_id=strategy_id,
                    variant=variant,
                    start=phase,
                    end=FULL_END,
                )
                for phase in PHASE_STARTS
            ]
            recent_results[strategy_id][variant] = [
                _run(
                    service,
                    membership,
                    strategy_id=strategy_id,
                    variant=variant,
                    start=phase,
                    end=FULL_END,
                )
                for phase in RECENT_PHASE_STARTS
            ]

    payload = {
        "purpose": "R98 historical HS300 membership and neutralization stress test",
        "engine": "StrategyBacktestService / project T+1 execution path",
        "cost": {
            "fees_pct": 0.0004,
            "commission_pct": 0.0004,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 10.0,
            "note": "2x-cost stress convention",
        },
        "membership": {
            "source": str(MEMBERSHIP.relative_to(ROOT)),
            "snapshot_start": str(membership["effective_date"].min()),
            "snapshot_end": str(membership["effective_date"].max()),
            "snapshot_dates": membership["effective_date"].n_unique(),
            "symbols": membership["symbol"].n_unique(),
            "join_semantics": "daily query_date snapshot, effective from that date",
        },
        "periods": {
            "full": [str(FULL_START), str(FULL_END)],
            "phase_starts": [str(day) for day in PHASE_STARTS],
            "recent_phase_starts": [str(day) for day in RECENT_PHASE_STARTS],
        },
        "strategies": STRATEGIES,
        "phase_strategies": PHASE_STRATEGIES,
        "variants": VARIANTS,
        "full": full,
        "phase_results": phase_results,
        "phase_summary": {
            strategy_id: {
                variant: _summary(records)
                for variant, records in variants.items()
            }
            for strategy_id, variants in phase_results.items()
        },
        "recent_results": recent_results,
        "recent_summary": {
            strategy_id: {
                variant: _summary(records)
                for variant, records in variants.items()
            }
            for strategy_id, variants in recent_results.items()
        },
        "caveats": [
            "Historical snapshots start on 2020-12-31 and are queried daily; 2021-06 start is fully covered.",
            "Snapshot query dates are treated as effective dates to avoid assuming membership before it was observed.",
            "Industry neutralization uses the repository industry map and can be limited by provider coverage.",
            "log_float_mv neutralization uses the enriched market-cap proxy; it is a style-exposure test, not a fundamental risk model.",
            "Fund-holding factors use current float shares to normalize historical holdings.",
            "Experimental strategies are not deployment-approved by this evidence.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
