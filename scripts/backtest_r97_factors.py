#!/usr/bin/env python3
"""R97: backtest the second batch of alternative factor strategies.

This batch covers shareholder flow, margin leverage, goodwill/R&D, equity
supply, dividend yield, insider net buying, market emotion, macro gates and a
cross-dimensional combo.  Only summaries are persisted; the R96 evidence file
keeps full trades for the first batch.
"""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r97_alt_factor_round2_backtests.json"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
PHASE_STARTS = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT_PHASE_STARTS = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
STRATEGIES = [
    "ai_hs300_holder_flow_quality",
    "ai_hs300_margin_quality",
    "ai_hs300_goodwill_quality",
    "ai_hs300_supply_quality",
    "ai_hs300_dividend_yield_quality",
    "ai_hs300_insider_net_quality",
    "ai_hs300_emotion_quality",
    "ai_hs300_macro_gate_quality",
    "ai_hs300_r97_combo",
]


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


def _cfg(strategy_id: str, start: date, end: date, max_positions: int = 10) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides={},
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


def _run(
    service: StrategyBacktestService,
    strategy_id: str,
    start: date,
    end: date,
    *,
    max_positions: int = 10,
) -> dict:
    started = time.perf_counter()
    result = service.run(_cfg(strategy_id, start, end, max_positions=max_positions))
    elapsed = round(time.perf_counter() - started, 3)
    record = {
        "strategy_id": strategy_id,
        "start": str(start),
        "end": str(end),
        "max_positions": max_positions,
        "error": result.error,
        "elapsed_seconds": elapsed,
        "stats": result.stats,
        "benchmark_return": _curve_return(result.benchmark_curve),
        "factor_attribution": result.factor_attribution,
    }
    print(
        f"{strategy_id} {start} p{max_positions}: err={result.error} "
        f"ret={result.stats.get('total_return')} sharpe={result.stats.get('sharpe')} "
        f"mdd={result.stats.get('max_drawdown')}",
        flush=True,
    )
    return record


def main() -> int:
    service = _service()
    full: dict[str, dict] = {}
    phases: dict[str, list[dict]] = {}
    recent: dict[str, list[dict]] = {}

    for sid in STRATEGIES:
        full[f"{sid}_p10"] = _run(service, sid, FULL_START, FULL_END, max_positions=10)
        full[f"{sid}_p20"] = _run(service, sid, FULL_START, FULL_END, max_positions=20)
        phases[sid] = [
            _run(service, sid, phase, FULL_END, max_positions=10)
            for phase in PHASE_STARTS
        ]
        recent[sid] = [
            _run(service, sid, phase, FULL_END, max_positions=10)
            for phase in RECENT_PHASE_STARTS
        ]

    payload = {
        "purpose": "R97 second alternative-factor strategy batch",
        "engine": "StrategyBacktestService / project T+1 execution path",
        "cost": {
            "fees_pct": 0.0004,
            "commission_pct": 0.0004,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 10.0,
            "note": "2x-cost stress convention",
        },
        "universe": "current HS300 membership (survivorship bias)",
        "periods": {
            "full": [str(FULL_START), str(FULL_END)],
            "phase_starts": [str(day) for day in PHASE_STARTS],
            "recent_phase_starts": [str(day) for day in RECENT_PHASE_STARTS],
        },
        "strategies": STRATEGIES,
        "full": full,
        "phase_results": phases,
        "phase_summary": {sid: _summary(records) for sid, records in phases.items()},
        "recent_phase_results": recent,
        "recent_phase_summary": {sid: _summary(records) for sid, records in recent.items()},
        "caveats": [
            "Experimental prototypes, not deployment approval.",
            "Current HS300 membership is applied to the full history.",
            "Research caches are local and not committed; rebuild with scripts/build_alt_factor_data.py.",
            "Some factors have low coverage or weak validation; see the per-strategy summaries.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
