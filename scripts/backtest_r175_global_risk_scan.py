#!/usr/bin/env python3
"""R175: global risk state timing scan for HS300."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r175_global_risk_scan.json"
CACHE = DATA / "research" / "r175_global_risk"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2018, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "global_risk_score"
CONTROL = "equity_risk_premium"
Z_WINDOW = 252
Z_MIN = 120
RET_WINDOW = 20


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fetch_us_index() -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "sp500.parquet"
    if path.exists():
        frame = pl.read_parquet(path)
        source = "cache"
    else:
        import akshare as ak
        raw = ak.index_us_stock_sina(symbol=".INX")
        frame = pl.from_pandas(raw).select(["date", "close"]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        frame.write_parquet(path)
        source = "akshare.index_us_stock_sina"
    return frame, {"source": source, "rows": frame.height, "start": str(frame["date"].min()), "end": str(frame["date"].max()), "missing_close": int(frame["close"].is_null().sum())}


def _fetch_bond() -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "bond_zh_us.parquet"
    if path.exists():
        frame = pl.read_parquet(path)
        source = "cache"
    else:
        import akshare as ak
        raw = ak.bond_zh_us_rate(start_date="20180101")
        frame = pl.from_pandas(raw).select([
            pl.col("日期").cast(pl.Date).alias("date"),
            pl.col("中国国债收益率10年").cast(pl.Float64).alias("cn10y"),
            pl.col("美国国债收益率10年").cast(pl.Float64).alias("us10y"),
        ]).sort("date")
        frame.write_parquet(path)
        source = "akshare.bond_zh_us_rate"
    return frame, {"source": source, "rows": frame.height, "start": str(frame["date"].min()), "end": str(frame["date"].max()), "missing_cn10y": int(frame["cn10y"].is_null().sum()), "missing_us10y": int(frame["us10y"].is_null().sum())}


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open", "close"])
        .collect()
        .sort("date")
    )


def _rank_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    x = pl.Series(xs).rank("average")
    y = pl.Series(ys).rank("average")
    value = pl.DataFrame({"x": x, "y": y}).select(pl.corr("x", "y")).item()
    if value is None or not math.isfinite(float(value)):
        return None
    return float(value)


def _residual_ic(rows: list[dict]) -> tuple[float | None, float | None]:
    usable = [r for r in rows if r[FACTOR] is not None and r[CONTROL] is not None and r["forward_return"] is not None]
    if len(usable) < 3:
        return None, None
    corr = _rank_corr([r[FACTOR] for r in usable], [r[CONTROL] for r in usable])
    xs = [r[CONTROL] for r in usable]
    ys = [r[FACTOR] for r in usable]
    n = len(usable)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return corr, None
    beta = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    residual = [y - alpha - beta * x for x, y in zip(xs, ys)]
    raw = _rank_corr(residual, [r["forward_return"] for r in usable])
    return corr, raw


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


def _qvix_like_control(day: date) -> float | None:
    path = DATA / "ext_data" / "alt_hs300" / "timeseries" / f"date={day.isoformat()}" / "part.parquet"
    if not path.exists():
        return None
    values = pl.read_parquet(path).select(CONTROL).drop_nulls()
    return float(values[CONTROL][0]) if values.height else None


def main() -> int:
    r167 = _load(R167_SCRIPT, "r167")
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}
    sp500, sp_audit = _fetch_us_index()
    bond, bond_audit = _fetch_bond()
    sp500 = sp500.with_columns((pl.col("close") / pl.col("close").shift(RET_WINDOW) - 1.0).alias("sp500_ret20d"))
    bond = bond.with_columns((pl.col("us10y") - pl.col("us10y").shift(RET_WINDOW)).alias("us10y_chg20d"))
    bond = bond.with_columns((pl.col("us10y") - pl.col("cn10y")).alias("us_cn_10y_spread"))
    combined = (
        sp500.select(["date", "sp500_ret20d"])
        .join(bond.select(["date", "us10y_chg20d", "us_cn_10y_spread"]), on="date", how="inner")
        .sort("date")
        .with_columns([
            ((pl.col("sp500_ret20d") - pl.col("sp500_ret20d").rolling_mean(Z_WINDOW, min_samples=Z_MIN)) / pl.col("sp500_ret20d").rolling_std(Z_WINDOW, min_samples=Z_MIN)).alias("z_sp500"),
            ((pl.col("us10y_chg20d") - pl.col("us10y_chg20d").rolling_mean(Z_WINDOW, min_samples=Z_MIN)) / pl.col("us10y_chg20d").rolling_std(Z_WINDOW, min_samples=Z_MIN)).alias("z_us10y"),
            ((pl.col("us_cn_10y_spread") - pl.col("us_cn_10y_spread").rolling_mean(Z_WINDOW, min_samples=Z_MIN)) / pl.col("us_cn_10y_spread").rolling_std(Z_WINDOW, min_samples=Z_MIN)).alias("z_spread"),
        ])
        .with_columns((-pl.col("z_us10y") + pl.col("z_sp500") - pl.col("z_spread")).alias(FACTOR))
    )
    rows = []
    for day in month_ends:
        i = index_pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            continue
        prior = combined.filter(pl.col("date") < day)
        if prior.is_empty():
            continue
        latest = prior.tail(1).to_dicts()[0]
        entry_day = index_dates[i + 1]
        exit_day = index_dates[i + 21]
        entry_open = index.filter(pl.col("date") == entry_day)["open"].item()
        exit_open = index.filter(pl.col("date") == exit_day)["open"].item()
        forward_return = float(exit_open / entry_open - 1.0)
        split = (
            "discovery" if DISCOVERY_START <= day <= DISCOVERY_END
            else "validation" if VALIDATION_START <= day <= VALIDATION_END
            else "holdout" if HOLDOUT_START <= day <= HOLDOUT_END
            else "other"
        )
        rows.append({
            "date": str(day),
            "split": split,
            "overseas_data_date": str(latest["date"]),
            FACTOR: latest.get(FACTOR),
            "sp500_ret20d": latest.get("sp500_ret20d"),
            "us10y_chg20d": latest.get("us10y_chg20d"),
            "us_cn_10y_spread": latest.get("us_cn_10y_spread"),
            CONTROL: _qvix_like_control(day),
            "forward_return": forward_return,
        })
    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split and r[FACTOR] is not None and r["forward_return"] is not None]
        raw = _rank_corr([r[FACTOR] for r in part], [r["forward_return"] for r in part])
        hits = [(r[FACTOR] < 0 and r["forward_return"] > 0) or (r[FACTOR] > 0 and r["forward_return"] < 0) for r in part if r[FACTOR] != 0]
        corr, residual = _residual_ic(part)
        summary[split] = {
            "months": len(part),
            "oriented_ic": round(raw, 6) if raw is not None else None,
            "sign_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "signal_positive_rate": round(sum(r[FACTOR] > 0 for r in part) / len(part), 4) if part else None,
            "erp_corr": round(corr, 6) if corr is not None else None,
            "residual_ic": round(residual, 6) if residual is not None else None,
            "long_cash": _long_cash(part),
        }
    holdout = [r for r in rows if r["split"] == "holdout"]
    recent = _long_cash(holdout[-12:]) if holdout else {"strategy_total": None}
    d1 = (
        len([r for r in rows if r[FACTOR] is not None]) >= 90
        and summary["discovery"]["months"] >= 50
        and summary["validation"]["months"] >= 24
        and summary["holdout"]["months"] >= 18
        and all(r["overseas_data_date"] < r["date"] for r in rows)
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
    g5 = summary["validation"]["erp_corr"] is not None and summary["validation"]["erp_corr"] < 0.70 and summary["validation"]["residual_ic"] is not None and summary["validation"]["residual_ic"] >= 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())
    result = {
        "purpose": "R175 global risk state timing",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {"sp500": sp_audit, "bond": bond_audit, "cache": str(CACHE.relative_to(ROOT))},
        "method": {
            "factor": "-z(us10y_chg20d) + z(sp500_ret20d) - z(us_cn_10y_spread)",
            "direction": "positive",
            "pit": "only overseas closes with date < China signal date T",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "factor > 0 long; otherwise cash",
            "cost_per_switch": COST,
            "z_window": Z_WINDOW,
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
            "US and China trading calendars differ; only the last overseas close strictly before T is used.",
            "No VIX series was available from the tested source; SP500 and Treasury yields are the global proxies.",
            "Monthly overlapping forward returns and a fixed zero threshold.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())