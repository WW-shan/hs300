#!/usr/bin/env python3
"""Phase robustness for the ERP=6.0 macro-gate variant."""
from __future__ import annotations

import json
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
OUT = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r97_macro_erp60_phases.json"
SID = "ai_hs300_macro_gate_quality_erp60"
END = date(2026, 10, 16)
PHASES = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT = [date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13), date(2026, 4, 15), date(2026, 6, 15)]


def main() -> int:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(DATA))), engine)

    def run(day: date) -> dict:
        result = service.run(StrategyBacktestConfig(
            strategy_id=SID, symbols=None, start=day, end=END, params={}, overrides={},
            matching="open_t+1", fees_pct=0.0004, commission_pct=0.0004,
            stamp_tax_pct=0.0005, slippage_bps=10.0, max_positions=10,
            position_sizing="equal", mode="position", asset_type="stock", hs300=True,
        ))
        return {
            "start": str(day), "error": result.error,
            "stats": result.stats,
            "benchmark_return": (
                result.benchmark_curve[-1]["value"] / result.benchmark_curve[0]["value"] - 1.0
                if result.benchmark_curve else None
            ),
        }

    phase_records = [run(day) for day in PHASES]
    recent_records = [run(day) for day in RECENT]
    def aggregate(records: list[dict]) -> dict:
        out = {}
        for key in ("total_return", "annual_return", "sharpe", "max_drawdown"):
            vals = [float(r["stats"][key]) for r in records if r["stats"].get(key) is not None]
            vals.sort()
            out[key] = {
                "mean": round(sum(vals) / len(vals), 6) if vals else None,
                "median": round(vals[len(vals) // 2], 6) if vals else None,
                "min": round(min(vals), 6) if vals else None,
                "max": round(max(vals), 6) if vals else None,
                "positive": sum(v > 0 for v in vals),
                "n": len(vals),
            }
        return out

    recent_signatures = {
        (r["stats"].get("total_return"), r["stats"].get("max_drawdown"), r["stats"].get("n_trades"))
        for r in recent_records
    }
    payload = {
        "strategy_id": SID,
        "params": {"erp_min": 6.0, "credit_max": 0.8, "term_min": 0.3},
        "phase_results": phase_records,
        "phase_summary": aggregate(phase_records),
        "recent_results": recent_records,
        "recent_summary": aggregate(recent_records),
        "recent_identical_paths": len(recent_signatures) == 1,
        "caveats": [
            "ERP=6.0 was selected from the same historical sample's sensitivity run.",
            "This is phase robustness, not independent out-of-sample validation.",
            "recent_identical_paths=true means the recent starts collapse to the same gated path; do not count them as five independent wins.",
            "Current HS300 membership introduces survivorship bias.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"phase": payload["phase_summary"], "recent": payload["recent_summary"]}, ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
