#!/usr/bin/env python3
"""One-at-a-time parameter sensitivity for the R97 macro-gate strategy."""
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
OUT = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r97_macro_gate_sensitivity.json"
FULL_START = date(2021, 6, 1)
FULL_END = date(2026, 10, 16)
RECENT_STARTS = [date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13), date(2026, 4, 15), date(2026, 6, 15)]
SID = "ai_hs300_macro_gate_quality"
DEFAULT = {"erp_min": 5.5, "credit_max": 0.8, "term_min": 0.3}
VARIANTS = [
    ("default", {}),
    ("erp_5.0", {"erp_min": 5.0}),
    ("erp_6.0", {"erp_min": 6.0}),
    ("erp_6.5", {"erp_min": 6.5}),
    ("credit_0.6", {"credit_max": 0.6}),
    ("credit_1.0", {"credit_max": 1.0}),
    ("term_0.2", {"term_min": 0.2}),
    ("term_0.4", {"term_min": 0.4}),
]


def _service() -> StrategyBacktestService:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(DATA))), engine)


def _cfg(start: date, params: dict) -> StrategyBacktestConfig:
    return StrategyBacktestConfig(
        strategy_id=SID, symbols=None, start=start, end=FULL_END,
        params={**DEFAULT, **params}, overrides={}, matching="open_t+1",
        fees_pct=0.0004, commission_pct=0.0004, stamp_tax_pct=0.0005,
        slippage_bps=10.0, max_positions=10, position_sizing="equal",
        mode="position", asset_type="stock", hs300=True,
    )


def _stats(result) -> dict:
    return {
        "total_return": result.stats.get("total_return"),
        "annual_return": result.stats.get("annual_return"),
        "sharpe": result.stats.get("sharpe"),
        "max_drawdown": result.stats.get("max_drawdown"),
        "n_trades": result.stats.get("n_trades"),
        "avg_exposure": result.stats.get("avg_exposure"),
    }


def _aggregate(records: list[dict]) -> dict:
    def values(key: str) -> list[float]:
        return [float(r[key]) for r in records if r.get(key) is not None]
    def summary(key: str) -> dict | None:
        vals = values(key)
        if not vals:
            return None
        ordered = sorted(vals)
        return {
            "mean": round(sum(vals) / len(vals), 6),
            "median": round(ordered[len(ordered) // 2], 6),
            "min": round(min(vals), 6),
            "max": round(max(vals), 6),
            "positive": sum(v > 0 for v in vals),
            "n": len(vals),
        }
    return {key: summary(key) for key in ("total_return", "annual_return", "sharpe", "max_drawdown")}


def main() -> int:
    service = _service()
    records = []
    for name, params in VARIANTS:
        started = time.perf_counter()
        full = service.run(_cfg(FULL_START, params))
        recent = [_stats(service.run(_cfg(day, params))) for day in RECENT_STARTS]
        recent_signatures = {
            (r.get("total_return"), r.get("max_drawdown"), r.get("n_trades"))
            for r in recent
        }
        record = {
            "variant": name,
            "params": {**DEFAULT, **params},
            "full": _stats(full),
            "recent": recent,
            "recent_summary": _aggregate(recent),
            "recent_identical_paths": len(recent_signatures) == 1,
            "error": full.error,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        records.append(record)
        recent_ret = record["recent_summary"].get("total_return")
        print(
            f"{name}: full_ret={record['full']['total_return']} full_sharpe={record['full']['sharpe']} "
            f"recent_mean={recent_ret['mean'] if recent_ret else None} "
            f"recent_positive={recent_ret['positive'] if recent_ret else 0}/5",
            flush=True,
        )
    payload = {
        "strategy_id": SID,
        "purpose": "one-at-a-time threshold sensitivity, not a parameter optimization search",
        "default": DEFAULT,
        "window": {"full": [str(FULL_START), str(FULL_END)], "recent_starts": [str(day) for day in RECENT_STARTS]},
        "variants": records,
        "caveats": [
            "Single-variable perturbations around a pre-existing default.",
            "The default was selected from the same historical sample; this is not out-of-sample proof.",
            "recent_identical_paths=true means different starts produced the same path because the gate blocked entries until the same date; it is not independent phase evidence.",
            "Current HS300 membership introduces survivorship bias.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
