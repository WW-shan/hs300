#!/usr/bin/env python3
"""R176: 300ETF variance risk premium timing scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r176_vrp_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
PROBE_SCRIPT = ROOT / "scripts" / "probe_r171_option_skew.py"
CACHE = DATA / "research" / "r171_option_skew"
DISCOVERY_START = date(2021, 1, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "vrp_z_expanding"
CONTROL_QVIX = "qvix_z_252"
CONTROL_SKEW = "skew_25d"
Z_MIN = 12


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open", "close"])
        .collect()
        .sort("date")
    )


def _atm_iv(day: date, probe) -> dict:
    ds = day.strftime("%Y%m%d")
    path = CACHE / f"risk_{ds}.parquet"
    if not path.exists():
        return {"atm_iv": None, "selected_expiry": None}
    risk = pd.read_parquet(path)
    risk = risk[risk["CONTRACT_ID"].astype(str).str.startswith("510300")].copy()
    parsed = []
    for row in risk.itertuples(index=False):
        meta = probe._parse_contract(str(row.CONTRACT_ID))
        if meta is not None:
            parsed.append({**meta, "delta": float(row.DELTA_VALUE), "iv": float(row.IMPLC_VOLATLTY)})
    if not parsed:
        return {"atm_iv": None, "selected_expiry": None}
    frame = pl.DataFrame(parsed)
    current_ym = day.strftime("%y%m")
    expiries = sorted(x for x in frame["expiry"].unique().to_list() if x > current_ym)
    selected = expiries[0] if expiries else None
    if selected is None:
        return {"atm_iv": None, "selected_expiry": None}
    part = (
        frame.filter(pl.col("expiry") == selected)
        .sort(["side", "strike", "marker"], descending=[False, False, True])
        .unique(subset=["side", "strike"], keep="first")
    )
    calls = [(float(r["delta"]), float(r["iv"])) for r in part.filter(pl.col("side") == "C").to_dicts()]
    puts = [(float(r["delta"]), float(r["iv"])) for r in part.filter(pl.col("side") == "P").to_dicts()]
    call_50 = probe._interp_iv(calls, 0.5)
    put_50 = probe._interp_iv(puts, -0.5)
    atm = (call_50 + put_50) / 2.0 if call_50 is not None and put_50 is not None else None
    return {"atm_iv": atm, "selected_expiry": selected, "call_iv_50": call_50, "put_iv_50": put_50}


def _control(day: date, column: str) -> float | None:
    path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return None
    values = pl.read_parquet(path).select(column).drop_nulls()
    return float(values[column][0]) if values.height else None


def _rank_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x = pl.Series(xs).rank("average")
    y = pl.Series(ys).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(rows: list[dict]) -> float | None:
    usable = [r for r in rows if r[FACTOR] is not None and r[CONTROL_QVIX] is not None and r[CONTROL_SKEW] is not None and r["forward_return"] is not None]
    if len(usable) < 12:
        return None
    residual = [r[FACTOR] for r in usable]
    for control in (CONTROL_QVIX, CONTROL_SKEW):
        xs = [r[control] for r in usable]
        n = len(xs)
        sx, sy = sum(xs), sum(residual)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, residual))
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            return None
        beta = (n * sxy - sx * sy) / denom
        alpha = (sy - beta * sx) / n
        residual = [y - alpha - beta * x for x, y in zip(xs, residual)]
    return _rank_corr(residual, [r["forward_return"] for r in usable])


def _pair_corr(rows: list[dict], control: str) -> float | None:
    pairs = [(r[FACTOR], r[control]) for r in rows if r[FACTOR] is not None and r[control] is not None]
    if len(pairs) < 3:
        return None
    return _rank_corr([x for x, _ in pairs], [y for _, y in pairs])


def _compound(values: list[float]) -> float:
    total = 1.0
    for value in values:
        total *= 1.0 + value
    return total - 1.0


def _mdd(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1.0 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def _long_cash(rows: list[dict]) -> dict:
    position = 0
    strategy = []
    benchmark = []
    switches = 0
    for row in rows:
        if row[FACTOR] is None or row["forward_return"] is None:
            continue
        target = 1 if row[FACTOR] > 0 else 0
        cost = COST if target != position else 0.0
        switches += int(target != position)
        position = target
        strategy.append(target * row["forward_return"] - cost)
        benchmark.append(row["forward_return"])
    return {
        "months": len(strategy),
        "strategy_total": round(_compound(strategy), 6) if strategy else None,
        "benchmark_total": round(_compound(benchmark), 6) if benchmark else None,
        "strategy_mdd": round(_mdd(strategy), 6) if strategy else None,
        "benchmark_mdd": round(_mdd(benchmark), 6) if benchmark else None,
        "strategy_mean": round(st.mean(strategy), 6) if strategy else None,
        "switches": switches,
    }


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    probe = _load(PROBE_SCRIPT, "probe_r171")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if date(2020, 1, 1) <= day <= HOLDOUT_END]
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}
    index = index.with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_ret")).with_columns(
        (pl.col("_ret").rolling_std(20, min_samples=20) * math.sqrt(252)).alias("rv20")
    )
    rv = {r["date"]: r["rv20"] for r in index.to_dicts()}
    rows = []
    for day in month_ends:
        atm = _atm_iv(day, probe)
        probe_row = probe._fetch_day(day)
        realized = rv.get(day)
        vrp = atm["atm_iv"] - realized if atm["atm_iv"] is not None and realized is not None else None
        rows.append({
            "date": str(day),
            "selected_expiry": atm["selected_expiry"],
            "atm_iv": atm["atm_iv"],
            "realized_vol_20d": realized,
            "vrp": vrp,
            CONTROL_SKEW: probe_row.get("skew_25d"),
            CONTROL_QVIX: _control(day, CONTROL_QVIX),
        })
    # Expanding z-score, strictly using current and prior VRP values.
    history: list[float] = []
    for row in rows:
        if row["vrp"] is not None:
            history.append(float(row["vrp"]))
        if len(history) >= Z_MIN and len(history) > 1:
            mean = st.mean(history)
            sd = st.pstdev(history)
            row[FACTOR] = (row["vrp"] - mean) / sd if sd > 0 else None
        else:
            row[FACTOR] = None
    for row in rows:
        day = date.fromisoformat(row["date"])
        i = index_pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            row["forward_return"] = None
        else:
            entry_day = index_dates[i + 1]
            exit_day = index_dates[i + 21]
            entry_open = index.filter(pl.col("date") == entry_day)["open"].item()
            exit_open = index.filter(pl.col("date") == exit_day)["open"].item()
            row["forward_return"] = float(exit_open / entry_open - 1.0)
        row["split"] = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split and r[FACTOR] is not None and r["forward_return"] is not None]
        raw = _rank_corr([r[FACTOR] for r in part], [r["forward_return"] for r in part])
        hits = [(r[FACTOR] < 0 and r["forward_return"] > 0) or (r[FACTOR] > 0 and r["forward_return"] < 0) for r in part if r[FACTOR] != 0]
        residual_ic = _residual_ic(part)
        qvix_corr = _pair_corr(part, CONTROL_QVIX)
        skew_corr = _pair_corr(part, CONTROL_SKEW)
        summary[split] = {
            "months": len(part),
            "oriented_ic": round(raw, 6) if raw is not None else None,
            "sign_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "signal_positive_rate": round(sum(r[FACTOR] > 0 for r in part) / len(part), 4) if part else None,
            "qvix_corr": round(qvix_corr, 6) if qvix_corr is not None else None,
            "skew_corr": round(skew_corr, 6) if skew_corr is not None else None,
            "residual_ic": round(residual_ic, 6) if residual_ic is not None else None,
            "long_cash": _long_cash(part),
        }
    holdout = [r for r in rows if r["split"] == "holdout"]
    recent = _long_cash(holdout[-12:]) if holdout else {"strategy_total": None}
    vrp_months = sum(r["vrp"] is not None for r in rows)
    signal_months = sum(r[FACTOR] is not None for r in rows)
    d1 = (
        vrp_months >= 75
        and signal_months >= 60
        and summary["discovery"]["months"] >= 24
        and summary["validation"]["months"] >= 24
        and summary["holdout"]["months"] >= 18
    )
    g1 = summary["discovery"]["oriented_ic"] is not None and summary["discovery"]["oriented_ic"] >= 0.15 and summary["discovery"]["sign_hit_rate"] >= 0.55
    g2 = summary["validation"]["oriented_ic"] is not None and summary["validation"]["oriented_ic"] >= 0.10 and summary["validation"]["sign_hit_rate"] >= 0.52
    g3 = summary["holdout"]["oriented_ic"] is not None and summary["holdout"]["oriented_ic"] >= 0.05 and summary["holdout"]["sign_hit_rate"] >= 0.52 and recent["strategy_total"] is not None and recent["strategy_total"] > 0
    g4 = all(
        summary[s]["long_cash"]["strategy_total"] is not None
        and summary[s]["long_cash"]["benchmark_total"] is not None
        and summary[s]["long_cash"]["strategy_total"] >= summary[s]["long_cash"]["benchmark_total"] - 0.02
        and summary[s]["long_cash"]["strategy_mdd"] is not None
        and summary[s]["long_cash"]["benchmark_mdd"] is not None
        and summary[s]["long_cash"]["strategy_mdd"] >= summary[s]["long_cash"]["benchmark_mdd"] + 0.02
        for s in ["validation", "holdout"]
    )
    max_corr = max(abs(summary["validation"]["qvix_corr"] or 0.0), abs(summary["validation"]["skew_corr"] or 0.0))
    g5 = max_corr < 0.70 and summary["validation"]["residual_ic"] is not None and summary["validation"]["residual_ic"] >= 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())
    result = {
        "purpose": "R176 300ETF variance risk premium timing",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {"cache": str(CACHE.relative_to(ROOT)), "vrp_months": vrp_months, "signal_months": signal_months},
        "method": {
            "factor": "expanding z-score of ATM implied vol minus HS300 20d realized vol",
            "direction": "positive",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "factor > 0 long; otherwise cash",
            "cost_per_switch": COST,
            "z_min_months": Z_MIN,
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_cash": recent,
        "decision": decision,
        "monthly_records": rows,
        "limitations": [
            "Options data are month-end SSE snapshots; intraday VRP is out of scope.",
            "ATM IV is interpolated in delta space from the nearest expiry.",
            "The expanding z-score uses current and prior month-end VRP only.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())