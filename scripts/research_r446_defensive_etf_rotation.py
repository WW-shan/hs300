#!/usr/bin/env python3
"""R446: long-history defensive ETF rotation research."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

RESEARCH_TYPE = "diagnostic_custom_simulation"

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r446_defensive_etf_rotation_preregistration.json"
OUTPUT = E / "r446_defensive_etf_rotation_2026-11-22.json"
RESULT = E / "r446_defensive_etf_rotation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_prices(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    paths = sorted((DATA / "kline_etf_enriched").glob("date=*/part.parquet"))
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & (pl.col("date") >= start) & (pl.col("date") <= end))
        .select(["symbol", "date", "open", "close"])
        .collect()
        .sort(["symbol", "date"])
    )
    return frame


def month_end_dates(frame: pl.DataFrame) -> list[date]:
    dates = sorted(frame["date"].unique().to_list())
    output = []
    for index, day in enumerate(dates):
        if index + 1 == len(dates) or dates[index + 1].month != day.month or dates[index + 1].year != day.year:
            output.append(day)
    return output


def pick_assets(frame: pl.DataFrame, signal_date: date, lookback: int, mode: str) -> list[str]:
    history = frame.filter(pl.col("date") <= signal_date)
    scores = []
    for symbol in frame["symbol"].unique().to_list():
        values = history.filter(pl.col("symbol") == symbol).sort("date")
        if values.height <= lookback:
            continue
        close = values["close"].to_numpy()
        start = float(close[-lookback - 1])
        end = float(close[-1])
        if start <= 0:
            continue
        ret = end / start - 1.0
        if mode == "risk_adjusted_momentum":
            returns = [float(close[i]) / float(close[i - 1]) - 1.0 for i in range(len(close) - lookback, len(close))]
            vol = (sum((x - sum(returns) / len(returns)) ** 2 for x in returns) / len(returns)) ** 0.5
            score = ret / vol if vol > 0 else -999.0
        else:
            score = ret
        scores.append((symbol, score))
    scores.sort(key=lambda item: item[1], reverse=True)
    return [symbol for symbol, _ in scores[:2]]


def simulate(frame: pl.DataFrame, rule: dict[str, Any], start: date, end: date, cost_bps: float) -> list[dict[str, Any]]:
    dates = sorted(frame["date"].unique().to_list())
    dates = [day for day in dates if start <= day <= end]
    if len(dates) < 2:
        return [{"date": str(day), "value": 1.0} for day in dates]
    by_symbol = {
        symbol: frame.filter(pl.col("symbol") == symbol).sort("date").select(["date", "open", "close"]).to_dicts()
        for symbol in frame["symbol"].unique().to_list()
    }
    lookup = {symbol: {row["date"]: row for row in rows} for symbol, rows in by_symbol.items()}
    signal_dates = set(month_end_dates(frame))
    holdings: dict[str, float] = {}
    curve: list[dict[str, Any]] = []
    value = 1.0
    # Start fully in cash; the first month-end signal buys at the next open.
    for index, day in enumerate(dates):
        if index > 0:
            # Settle the previous holdings over the interval ending at today's
            # open before applying a signal from yesterday's close. A position
            # bought at today's open must not earn yesterday-to-today return.
            previous_day = dates[index - 1]
            returns = []
            for symbol, weight in holdings.items():
                previous = lookup.get(symbol, {}).get(previous_day)
                current = lookup.get(symbol, {}).get(day)
                if previous and current and float(previous["open"]) > 0:
                    returns.append(weight * (float(current["open"]) / float(previous["open"]) - 1.0))
            value *= 1.0 + sum(returns)

            if previous_day in signal_dates:
                selected = pick_assets(frame, previous_day, int(rule["lookback"]), str(rule["mode"]))
                next_weights = {symbol: 1.0 / len(selected) for symbol in selected} if selected else {}
                turnover = sum(abs(next_weights.get(symbol, 0.0) - holdings.get(symbol, 0.0)) for symbol in set(next_weights) | set(holdings))
                value *= 1.0 - (cost_bps / 10000.0) * turnover
                holdings = next_weights
        curve.append({"date": str(day), "value": value})
    return curve

def metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    value = values[-1] / values[0] if values else 1.0
    mean = sum(rets) / len(rets) if rets else 0.0
    vol = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5 if rets else 0.0
    peak = values[0] if values else 1.0
    mdd = 0.0
    for item in values:
        peak = max(peak, item)
        mdd = min(mdd, item / peak - 1.0)
    return {"total_return": value - 1.0, "annual": value ** (252.0 / len(rets)) - 1.0 if rets else 0.0, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        path = [1.0]
        value = 1.0
        for ret in sampled[: len(values)]:
            value *= 1.0 + ret
            path.append(value)
        rets = [path[i] / path[i - 1] - 1.0 for i in range(1, len(path))]
        mean = sum(rets) / len(rets)
        vol = (sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(value - 1.0)
        sharpes.append(mean / vol * (252.0 ** 0.5) if vol else 0.0)
        mdds.append(mdd)
    def q(items, fraction):
        ordered = sorted(items)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {"total_p05": q(totals, .05), "total_p50": q(totals, .5), "sharpe_p50": q(sharpes, .5), "mdd_p05": q(mdds, .05)}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R446 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    symbols = list(prereg["universe"])
    frame = load_prices(symbols, date(2017, 12, 1), date(2026, 11, 20))
    rows = []
    for rule in prereg["rule_families"]:
        train = simulate(frame, rule, date.fromisoformat(prereg["windows"]["train_start"]), date.fromisoformat(prereg["windows"]["train_end"]), prereg["execution"]["cost_main_bps"])
        validation = simulate(frame, rule, date.fromisoformat(prereg["windows"]["validation_start"]), date.fromisoformat(prereg["windows"]["validation_end"]), prereg["execution"]["cost_main_bps"])
        test = simulate(frame, rule, date.fromisoformat(prereg["windows"]["test_start"]), date.fromisoformat(prereg["windows"]["test_end"]), prereg["execution"]["cost_main_bps"])
        recent = simulate(frame, rule, date.fromisoformat(prereg["windows"]["recent_start"]), date.fromisoformat(prereg["windows"]["test_end"]), prereg["execution"]["cost_main_bps"])
        rows.append({"name": rule["name"], "rule": rule, "train": metrics(train), "validation": metrics(validation), "test": metrics(test), "recent": metrics(recent), "test_bootstrap": bootstrap(test, block=20, paths=5000, seed=446), "recent_bootstrap": bootstrap(recent, block=20, paths=5000, seed=447)})
    eligible = [row for row in rows if row["train"]["annual"] >= prereg["gates"]["train_annual_min"] and row["validation"]["annual"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["name"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: min(row["train"]["annual"], row["validation"]["annual"]))
    checks = {"has_eligible_rule": bool(eligible), "test_annual_min": selected["test"]["annual"] >= prereg["gates"]["test_annual_min"], "test_sharpe_min": selected["test"]["sharpe"] >= prereg["gates"]["test_sharpe_min"], "test_mdd_min": selected["test"]["mdd"] >= prereg["gates"]["test_mdd_min"], "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0, "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0}
    payload = {"round": "R446", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "universe": prereg["universe"], "rows": rows, "selected": selected, "gate": {"pass": all(checks.values()), "checks": checks}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R446 防御 ETF 轮动", "", "| 规则 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['name']} | {row['train']['annual']:.2%} | {row['validation']['annual']:.2%} | {row['test']['annual']:.2%} | {row['test']['sharpe']:.3f} | {row['test']['mdd']:.2%} | {row['recent']['annual']:.2%} | {row['test_bootstrap']['total_p05']:.2%} | {row['recent_bootstrap']['total_p05']:.2%} |")
    lines += ["", f"- 选中：`{selected['name']}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
