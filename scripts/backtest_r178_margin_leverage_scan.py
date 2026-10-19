#!/usr/bin/env python3
"""R178: market-level margin leverage expansion timing scan."""
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
OUT = EVIDENCE / "r178_margin_leverage_scan.json"
CACHE = DATA / "research" / "r178_margin_account"
R167_SCRIPT = ROOT / "scripts" / "backtest_r167_target_price_scan.py"
DISCOVERY_START = date(2018, 2, 1)
DISCOVERY_END = date(2022, 12, 31)
VALIDATION_START = date(2023, 1, 1)
VALIDATION_END = date(2024, 12, 31)
HOLDOUT_START = date(2025, 1, 1)
HOLDOUT_END = date(2026, 8, 31)
COST = 0.0005
FACTOR = "margin_leverage_low"
CONTROLS = ["equity_risk_premium", "qvix_z_252"]
WINDOW = 20


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _margin_frame() -> tuple[pl.DataFrame, dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "margin_account.parquet"
    if path.exists():
        frame = pl.read_parquet(path)
        source = "cache"
    else:
        import akshare as ak
        raw = ak.stock_margin_account_info()
        frame = pl.from_pandas(raw).rename({
            "\u65e5\u671f": "date",
            "\u878d\u8d44\u4f59\u989d": "margin_balance",
            "\u878d\u5238\u4f59\u989d": "short_balance",
            "\u878d\u8d44\u4e70\u5165\u989d": "margin_buy",
            "\u878d\u5238\u5356\u51fa\u989d": "short_sell",
            "\u53c2\u4e0e\u4ea4\u6613\u7684\u6295\u8d44\u8005\u6570\u91cf": "active_participants",
            "\u6709\u878d\u8d44\u878d\u5238\u8d1f\u503a\u7684\u6295\u8d44\u8005\u6570\u91cf": "leveraged_investors",
            "\u62c5\u4fdd\u7269\u603b\u4ef7\u503c": "collateral_value",
            "\u5e73\u5747\u7ef4\u6301\u62c5\u4fdd\u6bd4\u4f8b": "maintenance_ratio",
        }).select([
            "date", "margin_balance", "short_balance", "margin_buy", "short_sell",
            "active_participants", "leveraged_investors", "collateral_value", "maintenance_ratio",
        ]).with_columns(pl.col("date").cast(pl.Date)).sort("date")
        frame.write_parquet(path)
        source = "akshare.stock_margin_account_info"
    return frame, {
        "source": source,
        "rows": frame.height,
        "start": str(frame["date"].min()),
        "end": str(frame["date"].max()),
        "missing_margin_balance": int(frame["margin_balance"].is_null().sum()),
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
    margin, audit = _margin_frame()
    margin = margin.with_columns(
        (pl.col("margin_balance") / pl.col("margin_balance").shift(WINDOW) - 1.0).alias("margin_balance_chg_20d")
    )
    margin = margin.with_columns((-pl.col("margin_balance_chg_20d")).alias(FACTOR))
    index = _index_daily()
    index_dates = index["date"].to_list()
    index_pos = {day: i for i, day in enumerate(index_dates)}
    rows = []
    for day in month_ends:
        prior = margin.filter(pl.col("date") <= day)
        if prior.is_empty():
            continue
        latest = prior.tail(1).to_dicts()[0]
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
            "data_date": str(latest["date"]),
            "split": split,
            "margin_balance_chg_20d": latest.get("margin_balance_chg_20d"),
            FACTOR: latest.get(FACTOR),
            "active_participants": latest.get("active_participants"),
            "maintenance_ratio": latest.get("maintenance_ratio"),
            "equity_risk_premium": _control(day, "equity_risk_premium"),
            "qvix_z_252": _control(day, "qvix_z_252"),
            "forward_return": forward_return,
        })
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
        and summary["discovery"]["months"] >= 24
        and summary["validation"]["months"] >= 24
        and summary["holdout"]["months"] >= 18
        and all(r["data_date"] <= r["date"] for r in rows)
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
        "purpose": "R178 market-level margin leverage expansion timing",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data": {**audit, "cache": str(CACHE.relative_to(ROOT)), "valid_months": valid_months},
        "method": {
            "factor": "negative 20-trading-day change in market margin balance",
            "direction": "positive",
            "forward_return": "HS300 T+1 open to T+21 open",
            "rule": "factor > 0 long; otherwise cash",
            "cost_per_switch": COST,
            "window": WINDOW,
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
            "Market-level margin balance is an aggregate, not a cross-sectional stock signal.",
            "The 20-day change is computed on consecutive available account snapshots.",
            "Long/cash timing uses a fixed zero threshold and 0.05% per switch.",
        ],
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "summary": summary, "recent_12m_long_cash": recent, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())