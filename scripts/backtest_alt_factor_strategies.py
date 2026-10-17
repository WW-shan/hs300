#!/usr/bin/env python3
"""Run the experimental alternative-factor strategies through the real engine.

This is deliberately an evidence generator, not a promotion gate.  It records
full-period and recent-year results, a max-position sensitivity, and an ERP
threshold sensitivity.  Results are written under the tracked research
iteration directory so the exact run can be reviewed later.
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
OUT = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r96_alt_factor_strategy_backtests.json"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
RECENT_START = date(2025, 10, 14)
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
    "ai_hs300_insider_buy_quality",
    "ai_hs300_holder_quality",
    "ai_hs300_dividend_quality",
    "ai_hs300_holder_erp_gate",
    "ai_hs300_financial_quality_value",
    "ai_hs300_alt_factor_combo",
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


def _config(
    strategy_id: str,
    start: date,
    end: date,
    *,
    params: dict | None = None,
    max_positions: int = 10,
) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params=params or {},
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


def _run(
    service: StrategyBacktestService,
    label: str,
    cfg: StrategyBacktestConfig,
    *,
    include_trades: bool = True,
) -> dict:
    started = time.perf_counter()
    result = service.run(cfg)
    elapsed = round(time.perf_counter() - started, 3)
    record = {
        "label": label,
        "strategy_id": cfg.strategy_id,
        "window": {"start": str(cfg.start), "end": str(cfg.end)},
        "params": cfg.params,
        "max_positions": cfg.max_positions,
        "error": result.error,
        "elapsed_seconds": elapsed,
        "stats": result.stats,
        "trade_count": len(result.trades),
        "benchmark_return": _curve_return(result.benchmark_curve),
        "factor_attribution": result.factor_attribution,
        "strategy_info": result.strategy_info,
        "trades": result.trades if include_trades else [],
    }
    print(
        f"{label}: error={result.error} trades={len(result.trades)} "
        f"return={result.stats.get('total_return')} sharpe={result.stats.get('sharpe')} "
        f"mdd={result.stats.get('max_drawdown')} elapsed={elapsed}s",
        flush=True,
    )
    return record


def _summary(records: list[dict]) -> dict:
    def values(key: str) -> list[float]:
        out = []
        for record in records:
            value = record.get("stats", {}).get(key)
            if value is not None:
                out.append(float(value))
        return out

    def stats(key: str) -> dict | None:
        vals = values(key)
        if not vals:
            return None
        ordered = sorted(vals)
        return {
            "mean": round(sum(vals) / len(vals), 6),
            "median": round(ordered[len(ordered) // 2], 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "positive": sum(value > 0 for value in vals),
            "n": len(vals),
        }

    return {
        "total_return": stats("total_return"),
        "annual_return": stats("annual_return"),
        "sharpe": stats("sharpe"),
        "max_drawdown": stats("max_drawdown"),
        "n_trades": stats("n_trades"),
    }


def main() -> int:
    service = _service()
    results: dict[str, dict] = {}

    for sid in STRATEGIES:
        results[f"full_{sid}_p10"] = _run(
            service, f"full {sid} p10", _config(sid, FULL_START, FULL_END, max_positions=10),
        )
        results[f"full_{sid}_p20"] = _run(
            service, f"full {sid} p20", _config(sid, FULL_START, FULL_END, max_positions=20),
        )
        results[f"recent_{sid}_p10"] = _run(
            service, f"recent {sid} p10", _config(sid, RECENT_START, FULL_END, max_positions=10),
        )

    phase_results: dict[str, list[dict]] = {}
    for sid in STRATEGIES:
        records = []
        for phase in PHASE_STARTS:
            record = _run(
                service,
                f"phase {sid} {phase}",
                _config(sid, phase, FULL_END, max_positions=10),
                include_trades=False,
            )
            record["phase_start"] = str(phase)
            records.append(record)
        phase_results[sid] = records

    recent_phase_results: dict[str, list[dict]] = {}
    for sid in STRATEGIES:
        records = []
        for phase in RECENT_PHASE_STARTS:
            record = _run(
                service,
                f"recent-phase {sid} {phase}",
                _config(sid, phase, FULL_END, max_positions=10),
                include_trades=False,
            )
            record["phase_start"] = str(phase)
            records.append(record)
        recent_phase_results[sid] = records

    for threshold in (5.0, 5.5, 6.0, 6.5, 7.0):
        results[f"erp_{threshold:.1f}"] = _run(
            service,
            f"ERP threshold {threshold:.1f}",
            _config(
                "ai_hs300_holder_erp_gate",
                FULL_START,
                FULL_END,
                params={"erp_min": threshold},
                max_positions=10,
            ),
        )

    payload = {
        "purpose": "R96 experimental alternative-factor strategy backtests",
        "engine": "StrategyBacktestService / project T+1 execution path",
        "cost": {
            "fees_pct": 0.0004,
            "commission_pct": 0.0004,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 10.0,
            "note": "2x-cost stress convention used for these experiments",
        },
        "universe": "current HS300 membership (survivorship bias)",
        "periods": {"full": [str(FULL_START), str(FULL_END)], "recent": [str(RECENT_START), str(FULL_END)]},
        "factors": [
            "ext_alt_hs300_insider_buy_20d",
            "ext_alt_hs300_holder_top10_free_float_share",
            "ext_alt_hs300_dividend_growth_ttm",
            "ext_alt_hs300_equity_risk_premium",
            "uf_quality_value_defensive",
            "uf_cash_quality",
        ],
        "results": results,
        "phase_results": {
            sid: [
                {
                    "phase_start": record["phase_start"],
                    "window": record["window"],
                    "stats": record["stats"],
                }
                for record in records
            ]
            for sid, records in phase_results.items()
        },
        "phase_summary": {sid: _summary(records) for sid, records in phase_results.items()},
        "recent_phase_results": {
            sid: [
                {
                    "phase_start": record["phase_start"],
                    "window": record["window"],
                    "stats": record["stats"],
                }
                for record in records
            ]
            for sid, records in recent_phase_results.items()
        },
        "recent_phase_summary": {
            sid: _summary(records) for sid, records in recent_phase_results.items()
        },
        "caveats": [
            "Experimental prototypes, not deployment approval.",
            "Current HS300 membership is applied to the full history; historical membership is not reconstructed here.",
            "The extension factors are built from local research caches and are not committed as data.",
            "ERP threshold sweep is in-sample and must not be interpreted as an optimal parameter.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
