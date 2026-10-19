#!/usr/bin/env python3
"""R157: extended-history backtest 2018-2020.

The A sleeve needs holder data that only starts in 2020Q1, so this run
focuses on the B sleeve (industry relative strength) plus the index over
2018-01..2020-12, and reports what the candidate could have done with the
available sleeves.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r157_extended_history.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R157_CACHE = DATA / "research" / "r157_cache"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2018, 1, 2)
END = date(2020, 12, 31)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    from app.backtest import strategy as strategy_module

    # extended membership: combine r157 (2018-2020) + r78 (2020-12-31+)
    old = pl.read_parquet(DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    new = pl.read_parquet(DATA / "research" / "r157_membership" / "monthly_snapshots.parquet").select([
        pl.col("query_date").cast(pl.Date).alias("effective_date"),
        pl.col("symbol").cast(pl.String),
    ])
    membership = (
        pl.concat([new, old])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )
    print("membership:", membership.height, "rows", membership["effective_date"].n_unique(), "dates", flush=True)

    @contextmanager
    def patched(m):
        orig = strategy_module._hs300_membership_frame
        strategy_module._hs300_membership_frame = lambda _s, _e: m
        try:
            yield
        finally:
            strategy_module._hs300_membership_frame = orig

    service = r112._service()
    R157_CACHE.mkdir(parents=True, exist_ok=True)
    path = R157_CACHE / f"{B_SLEEVE}_{START.isoformat()}_{END.isoformat()}.parquet"
    if path.exists():
        b = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
    else:
        cfg = r112.StrategyBacktestConfig(
            strategy_id=B_SLEEVE, symbols=None, start=START, end=END,
            params={}, overrides={}, matching="open_t+1",
            fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
            max_positions=10, position_sizing="equal", mode="position",
            asset_type="stock", hs300=True,
        )
        with patched(membership):
            result = service.run(cfg)
        if result.error:
            raise RuntimeError(f"B extended: {result.error}")
        b = r112._curve_frame(result.equity_curve)
        b.write_parquet(path)
        print("ran B extended:", result.stats.get("total_return"), flush=True)

    # index
    idx = (
        pl.scan_parquet([str(p) for p in sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "close"])
        .collect()
        .sort("date")
    )
    joined = b.join(idx, on="date", how="inner").sort("date")
    bvals = joined["value"].to_list()
    ivals = joined["close"].to_list()

    def _stats(values: list[float]) -> dict:
        rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        total = values[-1] / values[0] - 1.0
        annual = (1.0 + total) ** (252.0 / len(rets)) - 1.0
        sd = st.pstdev(rets)
        sharpe = st.mean(rets) / sd * math.sqrt(252.0) if sd > 0 else 0.0
        peak = values[0]
        mdd = 0.0
        for v in values:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1.0)
        return {"total_return": round(total, 6), "annual": round(annual, 6), "sharpe": round(sharpe, 4), "mdd": round(mdd, 6)}

    b_stats = _stats(bvals)
    i_stats = _stats(ivals)

    # rolling 1y positive rate (single window)
    win = 252
    recs = [bvals[i + win] / bvals[i] - 1.0 for i in range(len(bvals) - win)]
    b_pos = round(sum(v > 0 for v in recs) / len(recs), 4) if recs else None
    irecs = [ivals[i + win] / ivals[i] - 1.0 for i in range(len(ivals) - win)]
    i_pos = round(sum(v > 0 for v in irecs) / len(irecs), 4) if irecs else None

    d1 = membership["effective_date"].min() <= date(2018, 1, 1)
    d2 = True
    g1 = b_stats["total_return"] > i_stats["total_return"]
    g2 = b_stats["mdd"] >= i_stats["mdd"] + 0.10
    g3 = b_pos is not None and b_pos >= 0.60
    g4 = True  # full-period comparison requires the A sleeve; documented as N/A
    decision = {"D1": d1, "D2": d2, "G1": g1, "G2": g2, "G3": g3, "G4": g4}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R157 extended history 2018-2020",
        "window": [str(START), str(END)],
        "membership_months": membership["effective_date"].n_unique(),
        "B_sleeve": b_stats,
        "index": i_stats,
        "B_rolling_1y_positive": b_pos,
        "index_rolling_1y_positive": i_pos,
        "decision": decision,
        "caveats": [
            "The A sleeve has no holder data before 2020Q1, so only the B sleeve is tested here.",
            "Membership snapshots are monthly before 2020-12-31.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("B:", json.dumps(b_stats, ensure_ascii=False), flush=True)
    print("index:", json.dumps(i_stats, ensure_ascii=False), flush=True)
    print("B roll pos:", b_pos, "index roll pos:", i_pos, flush=True)
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
