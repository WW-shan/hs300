#!/usr/bin/env python3
"""R144: portfolio-layer and position-count sensitivity for the candidate."""
from __future__ import annotations

import importlib.util
import json
import statistics as st
from datetime import date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r144_portfolio_sensitivity.json"
R112_SCRIPT = ROOT / "scripts" / "backtest_r112_dual_sleeve_risk_parity.py"
R114_SCRIPT = ROOT / "scripts" / "backtest_r114_b_vol_target.py"
R127_SCRIPT = ROOT / "scripts" / "backtest_r127_vol_target_grid.py"
R125_CACHE = DATA / "research" / "r125_cache"
R119_CACHE = DATA / "research" / "r119_cache"
R144_CACHE = DATA / "research" / "r144_cache"
A_SLEEVE = "ai_hs300_macro_gate_quality"
B_SLEEVE = "ai_hs300_industry_relative_strength_quality_h20_sl20"
WINDOW = 252
WINDOWS = [40, 60, 90]
BOUNDS = [(0.2, 0.8), (0.3, 0.7), (0.1, 0.9)]
POSITIONS = [10, 15, 20]
PHASE = date(2021, 6, 1)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _combine_window(a: pl.DataFrame, b: pl.DataFrame, window: int, bounds: tuple[float, float]) -> pl.DataFrame:
    """Risk-parity combine with a parameterized trailing window."""
    joined = (
        a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
    )
    dates = joined["date"].to_list()
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    lo, hi = bounds
    targets = []
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if i < window:
                w = 0.5
            else:
                wa = [0.0, *ra][i - window : i]
                wb = [0.0, *rb][i - window : i]
                sa = max(st.pstdev(wa), 1e-6)
                sb = max(st.pstdev(wb), 1e-6)
                w = (1.0 / sa) / (1.0 / sa + 1.0 / sb)
                w = min(hi, max(lo, w))
            targets.append((day, w))
            prev_month = month
    target_map = {d: w for d, w in targets}
    portfolio = 1.0
    sa_ = 1.0
    sb_ = 1.0
    target = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    for i in range(1, len(dates)):
        day = dates[i]
        sa_ *= 1.0 + ra[i - 1]
        sb_ *= 1.0 + rb[i - 1]
        portfolio *= 1.0 + target * ra[i - 1] + (1.0 - target) * rb[i - 1]
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sa_ + (1.0 - target) * sb_
            actual = target * sa_ / total if total else target
            nt = target_map[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * (abs(nt - actual) + abs((1.0 - nt) - (1.0 - actual)))
            portfolio *= 1.0 - turnover * 0.0052
            sa_, sb_, target = 1.0, 1.0, nt
        rows.append({"date": day, "value": portfolio})
    return pl.DataFrame(rows)


def main() -> int:
    r112 = _load(R112_SCRIPT, "r112")
    r114 = _load(R114_SCRIPT, "r114")
    r127 = _load(R127_SCRIPT, "r127")
    r114.TARGET_VOL = 0.15
    R144_CACHE.mkdir(parents=True, exist_ok=True)

    # ---- Part A: portfolio layer ----
    grid = {}
    for window in WINDOWS:
        for bounds in BOUNDS:
            label = f"w{window}_b{int(bounds[0]*100)}-{int(bounds[1]*100)}"
            records = []
            for phase in r112.PHASE_STARTS + r112.RECENT_STARTS:
                key = phase.isoformat()
                a = pl.read_parquet(R125_CACHE / f"{A_SLEEVE}_{key}_{r112.END.isoformat()}_3x.parquet").with_columns(pl.col("date").cast(pl.Date))
                b = pl.read_parquet(R119_CACHE / f"{B_SLEEVE}_{key}_{r112.END.isoformat()}.parquet").with_columns(pl.col("date").cast(pl.Date))
                risk = _combine_window(a, b, window, bounds)
                cand = r114._vol_target(risk)["curve"]
                records.extend(r127._window_stats(cand["value"].to_list()))
            summary = r127._summarize(records)
            grid[label] = summary
            print(f"{label}: {json.dumps(summary, ensure_ascii=False)}", flush=True)

    # ---- Part B: position count ----
    membership = r112._historical_membership()
    service = r112._service()
    positions = {}
    for n in POSITIONS:
        curves = {}
        for label, sid in (("A", A_SLEEVE), ("B", B_SLEEVE)):
            path = R144_CACHE / f"{sid}_{PHASE.isoformat()}_{r112.END.isoformat()}_p{n}.parquet"
            if path.exists():
                curves[label] = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
                continue
            cfg = r112.StrategyBacktestConfig(
                strategy_id=sid, symbols=None, start=PHASE, end=r112.END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0006, commission_pct=0.0006, stamp_tax_pct=0.001, slippage_bps=15.0,
                max_positions=n, position_sizing="equal", mode="position",
                asset_type="stock", hs300=True,
            )
            with r112._patched_membership(membership):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{sid} p{n}: {result.error}")
            frame = r112._curve_frame(result.equity_curve)
            frame.write_parquet(path)
            curves[label] = frame
            print(f"ran {sid} p{n}: ret={result.stats.get('total_return')}", flush=True)
        risk, _ = r112._combine(curves["A"], curves["B"], "risk")
        cand = r114._vol_target(risk)["curve"]
        positions[f"p{n}"] = r127._summarize(r127._window_stats(cand["value"].to_list()))
        print(f"p{n}: {json.dumps(positions[f'p{n}'], ensure_ascii=False)}", flush=True)

    base = grid["w60_b20-80"]
    d1 = len(grid) == 9 and len(positions) == 3
    g1 = sum(1 for v in grid.values() if v["positive_rate"] >= 0.85) >= 7
    g2 = all(v["return_worst"] >= -0.20 for v in grid.values())
    g3 = abs(base["positive_rate"] - 0.8604) <= 0.001
    g4 = any(positions[f"p{n}"]["positive_rate"] >= positions["p10"]["positive_rate"] for n in (15, 20))
    g5 = all(v["return_worst"] >= -0.20 for v in positions.values())
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R144 portfolio-layer and position-count sensitivity",
        "portfolio_grid": grid,
        "position_grid": positions,
        "decision": decision,
        "caveats": ["Part B reruns only the longest phase.", "Windows overlap; descriptive only."],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("decision", decision, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
