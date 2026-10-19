#!/usr/bin/env python3
"""R179: IPO primary-market supply timing scan."""
from __future__ import annotations

import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
OUT = EVIDENCE / "r179_ipo_supply_scan.json"
CACHE = DATA / "research" / "r179_ipo"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2019, 1, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "ipo_supply_low"
CONTROLS = ["equity_risk_premium", "qvix_z_252"]
WINDOW_DAYS = 30
Z_MIN = 12


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _ipo_frame() -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "ipo.parquet"
    if path.exists():
        frame = pl.read_parquet(path)
        source = "cache"
    else:
        import akshare as ak
        raw = ak.stock_xgsglb_em(symbol="\u5168\u90e8\u80a1\u7968")
        frame = pl.from_pandas(raw).rename({
            "\u7533\u8d2d\u65e5\u671f": "apply_date",
            "\u53d1\u884c\u603b\u6570": "issue_shares",
            "\u53d1\u884c\u4ef7\u683c": "issue_price",
        }).select(["apply_date", "issue_shares", "issue_price"]).with_columns([
            pl.col("apply_date").cast(pl.Date),
            pl.col("issue_shares").cast(pl.Float64),
            pl.col("issue_price").cast(pl.Float64),
        ]).filter(
            pl.col("apply_date").is_not_null()
            & (pl.col("issue_shares") > 0)
            & (pl.col("issue_price") > 0)
        ).with_columns((pl.col("issue_shares") * pl.col("issue_price") / 10000.0).alias("issue_amount"))
        frame.write_parquet(path)
        source = "akshare.stock_xgsglb_em"
    return frame, {
        "source": source,
        "rows": frame.height,
        "start": str(frame["apply_date"].min()),
        "end": str(frame["apply_date"].max()),
    }


def _index_daily() -> pl.DataFrame:
    paths = sorted((DATA / "kline_index_daily").glob("date=*/part.parquet"))
    return (
        pl.scan_parquet([str(path) for path in paths])
        .filter(pl.col("symbol") == "000300.SH")
        .select(["date", "open", "close"])
        .collect()
        .sort("date")
    )


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
    usable = [r for r in rows if r[FACTOR] is not None and all(r[c] is not None for c in CONTROLS) and r["forward_return"] is not None]
    if len(usable) < 12:
        return None
    residual = [r[FACTOR] for r in usable]
    for control in CONTROLS:
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
    calendar = r167._calendar()
    month_ends = [day for day in r167._month_ends(calendar) if DISCOVERY_START <= day <= HOLDOUT_END]
    ipo, audit = _ipo_frame()
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}
    rows = []
    for day in month_ends:
        recent = ipo.filter((pl.col("apply_date") > day - timedelta(days=WINDOW_DAYS)) & (pl.col("apply_date") <= day))
        amount = float(recent["issue_amount"].sum()) if recent.height else 0.0
        count = recent.height
        i = index_pos.get(day)
        if i is None or i + 21 >= len(index_dates):
            forward_return = None
        else:
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
            "ipo_amount_30d": amount,
            "ipo_count_30d": count,
            "equity_risk_premium": _control(day, "equity_risk_premium"),
            "qvix_z_252": _control(day, "qvix_z_252"),
            "forward_return": forward_return,
        })
    history: list[float] = []
    for row in rows:
        history.append(float(row["ipo_amount_30d"]))
        if len(history) >= Z_MIN and len(history) > 1:
            mean = st.mean(history)
            sd = st.pstdev(history)
            z = (row["ipo_amount_30d"] - mean) / sd if sd > 0 else None
            row[FACTOR] = -z if z is not None else None
        else:
            row[FACTOR] = None
    summary = {}
    for split in ["discovery", "validation", "holdout"]:
        part = [r for r in rows if r["split"] == split and r[FACTOR] is not None and r["forward_return"] is not None]
        raw = _rank_corr([r[FACTOR] for r in part], [r["forward_return"] for r in part])
        hits = [(r[FACTOR] < 0 and r["forward_return"] > 0) or (r[FACTOR] > 0 and r["forward_return"] < 0) for r in part if r[FACTOR] != 0]
        summary[split] = {
            "months": len(part),
            "oriented_ic": round(raw, 6) if raw is not None else None,
            "sign_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "signal_positive_rate": round(sum(r[FACTOR] > 0 for r in part) / len(part), 4) if part else None,
            "erp_corr": _pair_corr(part, "equity_risk_premium"),
            "qvix_corr": _pair_corr(part, "qvix_z_252"),
            "residual_ic": _residual_ic(part),
            "long_cash": _long_cash(part),
        }
    holdout = [r for r in rows if r["split"] == "holdout"]
    recent = _long_cash(holdout[-12:]) if holdout else {"strategy_total": None}
    valid_months = sum(r[FACTOR] is not None for r in rows)
    d1 = (
        valid_months >= 90
        and summary["discovery"]["months"] >= 48
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
    max_corr = max(abs(summary["validation"]["erp_corr"] or 0.0), abs(summary["validation"]["qvix_corr"] or 0.0))
    g5 = max_corr < 0.70 and summary["validation"]["residual_ic"] is not None and summary["validation"]["residual_ic"] >= 0.05
    decision = {"D1": d1, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G5": g5}
    decision["all_pass"] = all(decision.values())
    result = {
        "purpose": "R179 IPO primary-market supply timing",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {**audit, "cache": str(CACHE.relative_to(ROOT)), "valid_months": valid_months},
        "method": {
            "factor": "negative expanding z-score of 30-day IPO issuance amount",
            "direction": "positive",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "factor > 0 long; otherwise cash",
            "cost_per_switch": COST,
            "window_days": WINDOW_DAYS,
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
            "IPO issuance amount is a market-level aggregate, not a cross-sectional signal.",
            "Only the 30 calendar days ending at T are used; no future listing or first-day return fields.",
            "Long/cash timing uses a fixed zero threshold and 0.05% per switch.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())