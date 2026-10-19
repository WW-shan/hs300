#!/usr/bin/env python3
"""R194: AH premium market-sentiment timing scan for HS300."""
from __future__ import annotations

import bisect
import importlib.util
import json
import math
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r194_ah_premium_scan.json"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2018, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTORS = ["ah_premium_z", "ah_premium_chg_1m"]
CONTROLS = ["qvix_z_252", "equity_risk_premium"]
CACHE = DATA / "research" / "r194_ah"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _hkd_cny() -> pl.DataFrame:
    path = CACHE / "hkd_cny.parquet"
    if path.exists():
        return pl.read_parquet(path).sort("date")
    raw = ak.currency_boc_safe()
    frame = (
        pl.from_pandas(raw[["日期", "港元"]])
        .rename({"日期": "date", "港元": "hkd_cny_100"})
        .with_columns(pl.col("date").cast(pl.Date))
        .filter(pl.col("hkd_cny_100").is_not_null())
        .sort("date")
        .with_columns((pl.col("hkd_cny_100") / 100.0).alias("hkd_cny"))
        .select(["date", "hkd_cny"])
    )
    frame.write_parquet(path)
    return frame


def _a_close() -> pl.DataFrame:
    paths = sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(p) for p in paths])
        .select(["symbol", "date", "raw_close"])
        .collect()
        .sort(["symbol", "date"])
    )


def _ah_daily() -> pl.DataFrame:
    h = pl.read_parquet(CACHE / "h_history.parquet")
    a = _a_close()
    fx = _hkd_cny()
    joined = (
        h.rename({"a_symbol": "symbol"}).join(a, on=["symbol", "date"], how="inner")
        .sort("date")
        .join_asof(fx, on="date", strategy="backward")
        .filter((pl.col("close") > 0) & (pl.col("raw_close") > 0) & (pl.col("hkd_cny") > 0))
        .with_columns((pl.col("raw_close") / (pl.col("close") * pl.col("hkd_cny")) - 1.0).alias("ah_premium"))
    )
    # cross-sectional median per day
    daily = (
        joined.group_by("date")
        .agg([
            pl.col("ah_premium").median().alias("ah_premium_median"),
            pl.len().alias("pairs"),
        ])
        .sort("date")
    )
    daily = daily.with_columns(
        pl.col("ah_premium_median").rolling_mean(window_size=20, min_samples=15).alias("ah_premium_median_20d")
    )
    return daily.filter(pl.col("ah_premium_median_20d").is_not_null())


def _index_forward(calendar: list[date], month_ends: list[date]) -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    index = (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open"])
        .collect()
        .sort("date")
    )
    index_dates = index["date"].to_list()
    pos = {day: i for i, day in enumerate(index_dates)}
    rows = []
    for day in month_ends:
        i = pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            continue
        entry_day = index_dates[i + 1]
        exit_day = index_dates[i + 21]
        entry = index.filter(pl.col("date") == entry_day)["open"].item()
        exit_ = index.filter(pl.col("date") == exit_day)["open"].item()
        if entry and exit_:
            rows.append({"rebalance_date": day, "forward_return": float(exit_ / entry - 1.0)})
    return pl.DataFrame(rows)


def _control_for_day(day: date, column: str) -> float | None:
    path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return None
    values = pl.read_parquet(path).select(column).drop_nulls()
    return float(values[column][0]) if values.height else None


def _rank_corr(values_x: list[float], values_y: list[float]) -> float | None:
    if len(values_x) < 3 or len(values_x) != len(values_y):
        return None
    x = pl.Series(values_x).rank("average")
    y = pl.Series(values_y).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(rows: list[dict], factor: str) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[factor] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    ys = [-r[factor] for r in usable]
    controls = [c for c in CONTROLS if all(r[c] is not None for r in usable)]
    corr_values = {}
    residual = ys
    for control in controls:
        xs = [r[control] for r in usable]
        n = len(usable)
        sx, sy = sum(xs), sum(residual)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, residual))
        denom = n * sxx - sx * sx
        if abs(denom) < 1e-12:
            continue
        beta = (n * sxy - sx * sy) / denom
        alpha = (sy - beta * sx) / n
        residual = [y - alpha - beta * x for x, y in zip(xs, residual)]
        corr_values[control] = _rank_corr([r[factor] for r in usable], xs)
    forward = [r["forward_return"] for r in usable]
    resid_ic = _rank_corr(residual, forward)
    max_corr = max((abs(v) for v in corr_values.values() if v is not None), default=None)
    return max_corr, resid_ic


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


def _long_cash(rows: list[dict], factor: str) -> dict:
    position = 0
    strategy_returns = []
    benchmark_returns = []
    switches = 0
    for row in rows:
        signal = row[factor]
        if signal is None or row["forward_return"] is None:
            continue
        target = 1 if signal < 0 else 0
        cost = COST if target != position else 0.0
        switches += int(target != position)
        position = target
        strategy_returns.append(target * row["forward_return"] - cost)
        benchmark_returns.append(row["forward_return"])
    return {
        "months": len(strategy_returns),
        "strategy_total": round(_compound(strategy_returns), 6) if strategy_returns else None,
        "benchmark_total": round(_compound(benchmark_returns), 6) if benchmark_returns else None,
        "strategy_mdd": round(_mdd(strategy_returns), 6) if strategy_returns else None,
        "benchmark_mdd": round(_mdd(benchmark_returns), 6) if benchmark_returns else None,
        "switches": switches,
    }


def _ic_hit(rows: list[dict], factor: str) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[factor] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    ic = _rank_corr([-r[factor] for r in usable], [r["forward_return"] for r in usable])
    hit = sum(1 for r in usable if (r[factor] < 0) == (r["forward_return"] > 0)) / len(usable)
    return ic, round(hit, 4)


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    ah = _ah_daily()
    forward = _index_forward(calendar, month_ends)

    ah = ah.with_columns(
        pl.col("ah_premium_median_20d").shift(1).alias("_prev"),
        pl.int_range(1, pl.len() + 1).alias("_n"),
    )
    # expanding z with >=12 observations, computed on month ends only
    month_end_set = set(month_ends)
    month_rows = ah.filter(pl.col("date").is_in(list(month_end_set))).sort("date")
    month_rows = month_rows.with_columns(
        pl.int_range(1, pl.len() + 1).alias("_m")
    ).with_columns([
        pl.when(pl.col("_m") >= 12).then(pl.col("ah_premium_median_20d").cum_sum() / pl.col("_m")).otherwise(None).alias("_mean"),
    ]).with_columns(
        pl.when(pl.col("_m") >= 12).then(
            ((pl.col("ah_premium_median_20d") ** 2).cum_sum() / pl.col("_m") - pl.col("_mean") ** 2).clip(lower_bound=0.0).sqrt()
        ).otherwise(None).alias("_std")
    ).with_columns([
        pl.when((pl.col("_m") >= 12) & (pl.col("_std") > 0))
        .then((pl.col("ah_premium_median_20d") - pl.col("_mean")) / pl.col("_std")).otherwise(None).alias("ah_premium_z"),
        (pl.col("ah_premium_median_20d") - pl.col("ah_premium_median_20d").shift(1)).alias("ah_premium_chg_1m"),
        pl.col("pairs"),
    ]).select(["date", "ah_premium_median_20d", "ah_premium_z", "ah_premium_chg_1m", "pairs"])

    ah_map = {r["date"]: r for r in month_rows.iter_rows(named=True)}
    ah_dates = month_rows["date"].to_list()

    rows = []
    for row in forward.iter_rows(named=True):
        day = row["rebalance_date"]
        pos = bisect.bisect_right(ah_dates, day)
        if pos == 0:
            continue
        rec = ah_map[ah_dates[pos - 1]]
        record = {
            "date": str(day),
            "forward_return": row["forward_return"],
            "ah_premium_median_20d": rec["ah_premium_median_20d"],
            "ah_premium_z": rec["ah_premium_z"],
            "ah_premium_chg_1m": rec["ah_premium_chg_1m"],
            "pairs": rec["pairs"],
        }
        for control in CONTROLS:
            record[control] = _control_for_day(day, control)
        rows.append(record)

    def split_of(day: date) -> str:
        if DISCOVERY_START <= day <= DISCOVERY_END:
            return "discovery"
        if VALIDATION_START <= day <= VALIDATION_END:
            return "validation"
        if HOLDOUT_START <= day <= HOLDOUT_END:
            return "holdout"
        return "other"

    for row in rows:
        row["split"] = split_of(date.fromisoformat(row["date"]))

    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split]
        entry = {"months": len(part)}
        for factor in FACTORS:
            ic, hit = _ic_hit(part, factor)
            corr, resid = _residual_ic(part, factor)
            entry[factor] = {
                "ic": round(ic, 6) if ic is not None else None,
                "hit_rate": hit,
                "long_cash": _long_cash(part, factor),
                "max_control_corr": round(corr, 6) if corr is not None else None,
                "residual_ic": round(resid, 6) if resid is not None else None,
            }
        entry["min_pairs"] = min((r["pairs"] for r in part), default=None)
        summary[split] = entry

    holdout_rows = [r for r in rows if r["split"] == "holdout"]
    recent_rows = holdout_rows[-12:]
    recent_lc = {f: _long_cash(recent_rows, f) for f in FACTORS}

    total_months = len(rows)
    results = {}
    for factor in FACTORS:
        d = summary["discovery"][factor]
        v = summary["validation"][factor]
        h = summary["holdout"][factor]
        signal_months = sum(1 for r in rows if r[factor] is not None)
        coverage = signal_months / total_months if total_months else 0.0
        min_pairs = min((r["pairs"] for r in rows), default=0)
        d1 = total_months >= 90 and coverage >= 0.90 and min_pairs >= 30
        g1 = d["ic"] is not None and d["ic"] >= 0.15 and d["hit_rate"] >= 0.55
        g2 = v["ic"] is not None and v["ic"] >= 0.10 and v["hit_rate"] >= 0.52
        g3 = (
            h["ic"] is not None and h["ic"] >= 0.05 and h["hit_rate"] >= 0.52
            and recent_lc[factor]["strategy_total"] is not None and recent_lc[factor]["strategy_total"] > 0
        )
        g4 = all(
            summary[s][factor]["long_cash"]["strategy_total"] is not None
            and summary[s][factor]["long_cash"]["benchmark_total"] is not None
            and summary[s][factor]["long_cash"]["strategy_total"] >= summary[s][factor]["long_cash"]["benchmark_total"] - 0.02
            and summary[s][factor]["long_cash"]["strategy_mdd"] is not None
            and summary[s][factor]["long_cash"]["strategy_mdd"] >= summary[s][factor]["long_cash"]["benchmark_mdd"] - 0.02
            for s in ["validation", "holdout"]
        )
        g5 = (
            v["max_control_corr"] is not None and v["max_control_corr"] < 0.70
            and v["residual_ic"] is not None and v["residual_ic"] >= 0.05
        )
        decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
        decision["all_pass"] = all(decision.values())
        results[factor] = decision

    result = {
        "purpose": "R194 AH premium market-sentiment timing scan",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "source": "Tencent stock_zh_ah_daily + BOC HKD/CNY + local enriched A close",
            "pairs": int(ah["pairs"].max()),
            "months": total_months,
            "start": rows[0]["date"] if rows else None,
            "end": rows[-1]["date"] if rows else None,
            "min_pairs": min_pairs,
        },
        "method": {
            "factors": {
                "ah_premium_z": "expanding z-score of 20d median A-H premium, direction negative",
                "ah_premium_chg_1m": "monthly change of 20d median A-H premium, direction negative",
            },
            "long_cash": "signal < 0 holds HS300 index, else cash; 0.05% per switch",
            "forward_return": "T+1 open to T+21 open on 000300.SH",
            "splits": {
                "discovery": [str(DISCOVERY_START), str(DISCOVERY_END)],
                "validation": [str(VALIDATION_START), str(VALIDATION_END)],
                "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            },
        },
        "summary": summary,
        "recent_12m_long_cash": recent_lc,
        "decision": results,
        "monthly_records": rows,
        "limitations": [
            "Only 92 A-H pairs are available; HS300 membership is matched by name and may miss renamed companies.",
            "H-share close and A-share close are same-day but HKD/CNY uses the BOC central parity, not intraday FX.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "data": result["data"], "summary": summary, "recent_12m": recent_lc, "decision": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
