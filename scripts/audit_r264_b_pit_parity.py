#!/usr/bin/env python3
"""R264-B: rebuild PIT industry data and re-run the B sleeve for parity check."""

from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r264_b_pit_parity.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R119_SCRIPT = ROOT / "scripts" / "backtest_r119_pit_industry_vol_target.py"
CACHE = DATA / "research" / "r264_cache"
SID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
CACHED = DATA / "research" / "r119_cache" / f"{SID}_{START.isoformat()}_{END.isoformat()}.parquet"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112_r264b")
    r119 = _load(R119_SCRIPT, "r119_r264b")

    swap_info = r119._rebuild_industry_factors()
    try:
        membership = r112._historical_membership()
        service = r112._service()
        cfg = r112.StrategyBacktestConfig(
            strategy_id=SID, symbols=None, start=START, end=END,
            params={}, overrides={}, matching="open_t+1",
            fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
            max_positions=10, position_sizing="equal", mode="position",
            asset_type="stock", hs300=True,
        )
        started = time.perf_counter()
        with r112._patched_membership(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(result.error)
        curve = r112._curve_frame(result.equity_curve)
        trades = [
            {
                "symbol": t.get("symbol"),
                "name": t.get("name"),
                "entry_date": str(t.get("entry_date")),
                "exit_date": str(t.get("exit_date")),
                "pnl_pct": t.get("pnl_pct"),
                "pnl_amount": t.get("pnl_amount"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in result.trades
        ]
        CACHE.mkdir(parents=True, exist_ok=True)
        curve.write_parquet(CACHE / f"{SID}_pit_{START.isoformat()}_{END.isoformat()}.parquet")
        (CACHE / f"{SID}_pit_{START.isoformat()}_{END.isoformat()}.trades.json").write_text(
            json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    finally:
        r119._restore_ext()

    cached = pl.read_parquet(CACHED).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    joined = (
        cached.rename({"value": "cached"})
        .join(curve.rename({"value": "rerun"}), on="date", how="inner")
        .sort("date")
    )
    joined = joined.with_columns(
        (pl.col("cached") / pl.col("cached")[0]).alias("cached_rebased"),
        (pl.col("rerun") / pl.col("rerun")[0]).alias("rerun_rebased"),
    )
    diff = (joined["cached_rebased"] - joined["rerun_rebased"]).abs()
    parity = {
        "days": joined.height,
        "cached_total_return": round(float(cached["value"][-1] / cached["value"][0] - 1.0), 6),
        "rerun_total_return": round(float(curve["value"][-1] / curve["value"][0] - 1.0), 6),
        "max_abs_diff": round(float(diff.max() or 0.0), 12),
        "mean_abs_diff": round(float(diff.mean() or 0.0), 12),
        "pass": bool(joined.height > 0 and float(diff.max() or 0.0) <= 1e-6),
    }
    payload = {
        "round": "R264-B",
        "purpose": "Check whether the B sleeve re-run with PIT Shenwan industry reproduces the r119 cache.",
        "swap": swap_info,
        "parity": parity,
        "trades": len(trades),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
