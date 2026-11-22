#!/usr/bin/env python3
"""R454: value-quality HS300 sleeve mixed with defensive ETFs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r454_value_quality_defensive_mix_preregistration.json"
OUTPUT = E / "r454_value_quality_defensive_mix_2026-11-22.json"
RESULT = E / "r454_value_quality_defensive_mix_2026-11-22.md"
R453_PREREG = E / "r453_value_quality_portfolio_preregistration.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def path_metrics(curve: list[dict[str, Any]]) -> dict[str, float]:
    values = [float(item["value"]) for item in curve]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0 if values else 0.0
    annual = (1.0 + total) ** (252.0 / len(returns)) - 1.0 if returns else 0.0
    mean = sum(returns) / len(returns) if returns else 0.0
    vol = (sum((value - mean) ** 2 for value in returns) / len(returns)) ** 0.5 if returns else 0.0
    peak = values[0] if values else 1.0
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {"total_return": total, "annual": annual, "sharpe": mean / vol * (252.0 ** 0.5) if vol else 0.0, "mdd": mdd}


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0 for index in range(1, len(curve))]
    rng = random.Random(seed)
    totals, sharpes, mdds = [], [], []
    for _ in range(paths):
        sampled = []
        while len(sampled) < len(values):
            start = rng.randrange(len(values))
            sampled.extend(values[(start + offset) % len(values)] for offset in range(block))
        selected = sampled[: len(values)]
        value = 1.0
        path = [1.0]
        for item in selected:
            value *= 1.0 + item
            path.append(value)
        returns = [path[index] / path[index - 1] - 1.0 for index in range(1, len(path))]
        mean = sum(returns) / len(returns)
        vol = (sum((item - mean) ** 2 for item in returns) / len(returns)) ** 0.5
        peak = path[0]
        mdd = 0.0
        for item in path:
            peak = max(peak, item)
            mdd = min(mdd, item / peak - 1.0)
        totals.append(value - 1.0)
        sharpes.append(mean / vol * (252.0 ** 0.5) if vol else 0.0)
        mdds.append(mdd)
    def q(items: list[float], fraction: float) -> float:
        ordered = sorted(items)
        return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]
    return {"total_p05": q(totals, 0.05), "total_p50": q(totals, 0.5), "sharpe_p50": q(sharpes, 0.5), "mdd_p05": q(mdds, 0.05)}


def load_etf(symbols: list[str], start: date, end: date) -> pl.DataFrame:
    paths = sorted((DATA / "kline_etf_enriched").glob("date=*/part.parquet"))
    if not paths:
        raise FileNotFoundError("R454 requires local data/kline_etf_enriched")
    frame = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .filter(pl.col("symbol").is_in(symbols) & (pl.col("date") >= start) & (pl.col("date") <= end))
        .select(["symbol", "date", "open", "close"])
        .collect()
        .sort(["symbol", "date"])
    )
    missing = sorted(set(symbols) - set(frame["symbol"].unique().to_list()))
    if missing:
        raise FileNotFoundError(f"missing ETF history: {missing}")
    return frame


def etf_curve(frame: pl.DataFrame, symbols: list[str], mode: str, start: date, end: date, cost_bps: float) -> list[dict[str, Any]]:
    dates = [day for day in sorted(frame["date"].unique().to_list()) if start <= day <= end]
    if not dates:
        return []
    lookup = {symbol: {row["date"]: row for row in frame.filter(pl.col("symbol") == symbol).to_dicts()} for symbol in symbols}
    month_ends = set()
    all_dates = sorted(frame["date"].unique().to_list())
    for index, day in enumerate(all_dates):
        if index + 1 == len(all_dates) or all_dates[index + 1].month != day.month or all_dates[index + 1].year != day.year:
            month_ends.add(day)
    if mode == "fixed_equal":
        weights = {symbol: 1.0 / len(symbols) for symbol in symbols}
    elif mode == "bond_gold_equal":
        weights = {"511010.SH": 0.5, "518880.SH": 0.5}
    else:
        raise ValueError(f"unsupported ETF mode: {mode}")
    holdings: dict[str, float] = {}
    curve = []
    value = 1.0
    for index, day in enumerate(dates):
        if index > 0:
            previous = dates[index - 1]
            returns = []
            for symbol, weight in holdings.items():
                old = lookup.get(symbol, {}).get(previous)
                new = lookup.get(symbol, {}).get(day)
                if old and new and float(old["open"]) > 0:
                    returns.append(weight * (float(new["open"]) / float(old["open"]) - 1.0))
            value *= 1.0 + sum(returns)
            if previous in month_ends:
                turnover = sum(abs(weights.get(symbol, 0.0) - holdings.get(symbol, 0.0)) for symbol in set(weights) | set(holdings))
                value *= 1.0 - (cost_bps / 10000.0) * turnover
                holdings = dict(weights)
        curve.append({"date": str(day), "value": value})
    return curve


def align_curve(equity: list[dict[str, Any]], etf: list[dict[str, Any]], mix: dict[str, Any]) -> list[dict[str, Any]]:
    equity_by_date = {str(item["date"]): float(item["value"]) for item in equity}
    etf_by_date = {str(item["date"]): float(item["value"]) for item in etf}
    dates = sorted(set(equity_by_date) & set(etf_by_date))
    eq_weight = float(mix["equity_weight"])
    def_weight = float(mix["defensive_weight"])
    cash_weight = float(mix["cash_weight"])
    if abs(eq_weight + def_weight + cash_weight - 1.0) > 1e-12:
        raise ValueError("weights must sum to one")
    # Use normalized sleeve returns, preserving both sleeves' execution semantics.
    out = []
    for index, day in enumerate(dates):
        if index == 0:
            value = 1.0
        else:
            previous = dates[index - 1]
            eq_ret = equity_by_date[day] / equity_by_date[previous] - 1.0
            etf_ret = etf_by_date[day] / etf_by_date[previous] - 1.0
            value *= 1.0 + eq_weight * eq_ret + def_weight * etf_ret
        out.append({"date": day, "value": value})
    return out


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R454 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R454 preregistration checksum mismatch")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r454_r453")
    p5 = r453.load_p5()
    r453_prereg = json.loads(R453_PREREG.read_text(encoding="utf-8"))
    panel, symbols = r453.build_panel(p5, r453_prereg)
    service = p5.portfolio._service()
    directions = {"value_quality": "high"}
    etf_symbols = list(prereg["defensive_etfs"])
    windows = prereg["windows"]
    # Train/validation equity sleeve only; defensive ETF histories are evaluated in the same windows.
    equity_runs = {}
    for positions in prereg["grid"]["equity_positions"]:
        for hold in prereg["grid"]["equity_hold_days"]:
            key = f"{positions}_{hold}"
            equity_runs[key] = {}
            for window in ("train", "validation"):
                equity_runs[key][window] = r453.run_variant(
                    p5, service, panel, symbols, {"value_quality": 1.0}, directions,
                    start=windows[f"{window}_start"], end=windows[f"{window}_end"],
                    positions=positions, hold=hold, cost_label="2x",
                )
    etf_frame = load_etf(etf_symbols, date.fromisoformat(windows["train_start"]), date.fromisoformat(windows["test_end"]))
    etf_runs = {}
    for mix in prereg["mix_variants"]:
        etf_runs[mix["name"]] = {}
        for window in ("train", "validation", "test", "recent"):
            etf = etf_curve(etf_frame, etf_symbols, mix["defensive_mode"], date.fromisoformat(windows[f"{window}_start"]), date.fromisoformat(windows["test_end"] if window == "recent" else windows[f"{window}_end"]), float(prereg["execution"]["etf_cost_main_bps"]))
            etf_runs[mix["name"]][window] = etf
    rows = []
    for key, equity in equity_runs.items():
        for mix in prereg["mix_variants"]:
            if mix["equity_weight"] <= 0:
                continue
            row = {"equity_grid": key, "mix": mix["name"]}
            for window in ("train", "validation"):
                row[window] = path_metrics(align_curve(equity[window]["curve"], etf_runs[mix["name"]][window], mix))
            rows.append(row)
    eligible = [row for row in rows if row["train"]["annual"] >= prereg["gates"]["train_annual_min"] and row["validation"]["annual"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["equity_grid"], row["mix"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: min(row["train"]["annual"], row["validation"]["annual"]))
    positions, hold = map(int, selected["equity_grid"].split("_"))
    mix = next(item for item in prereg["mix_variants"] if item["name"] == selected["mix"])
    test_equity = r453.run_variant(p5, service, panel, symbols, {"value_quality": 1.0}, directions, start=windows["test_start"], end=windows["test_end"], positions=positions, hold=hold, cost_label="2x")
    test_etf = etf_curve(etf_frame, etf_symbols, mix["defensive_mode"], date.fromisoformat(windows["test_start"]), date.fromisoformat(windows["test_end"]), float(prereg["execution"]["etf_cost_main_bps"]))
    test_curve = align_curve(test_equity["curve"], test_etf, mix)
    stress_etf = etf_curve(etf_frame, etf_symbols, mix["defensive_mode"], date.fromisoformat(windows["test_start"]), date.fromisoformat(windows["test_end"]), float(prereg["execution"]["etf_cost_stress_bps"]))
    stress_curve = align_curve(test_equity["curve"], stress_etf, mix)
    recent_equity = r453.run_variant(p5, service, panel, symbols, {"value_quality": 1.0}, directions, start=windows["recent_start"], end=windows["test_end"], positions=positions, hold=hold, cost_label="2x")
    recent_etf = etf_curve(etf_frame, etf_symbols, mix["defensive_mode"], date.fromisoformat(windows["recent_start"]), date.fromisoformat(windows["test_end"]), float(prereg["execution"]["etf_cost_main_bps"]))
    recent_curve = align_curve(recent_equity["curve"], recent_etf, mix)
    test_stats = path_metrics(test_curve)
    stress_stats = path_metrics(stress_curve)
    recent_stats = path_metrics(recent_curve)
    test_bootstrap = bootstrap(test_curve, block=20, paths=5000, seed=454)
    recent_bootstrap = bootstrap(recent_curve, block=20, paths=5000, seed=455)
    equity_only = path_metrics(test_equity["curve"])
    checks = {
        "train_annual_min": selected["train"]["annual"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["annual"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": test_stats["annual"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": test_stats["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": test_stats["mdd"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": recent_stats["annual"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": stress_stats["annual"] > 0,
        "test_total_p05_positive": test_bootstrap["total_p05"] > 0,
        "recent_total_p05_positive": recent_bootstrap["total_p05"] > 0,
        "equity_plus_defensive_improves_tail": test_stats["mdd"] >= equity_only["mdd"] or recent_stats["mdd"] >= equity_only["mdd"],
        "no_leverage": True,
    }
    payload = {
        "round": "R454",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "rows": rows,
        "selected": selected,
        "selected_mix": mix,
        "test": test_stats,
        "test_3x": stress_stats,
        "recent": recent_stats,
        "test_bootstrap": test_bootstrap,
        "recent_bootstrap": recent_bootstrap,
        "equity_only_test": equity_only,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "five_x_hard_rejection": False},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R454 财报质量 + 防御资产混合", "", "| 股票仓数/持有 | 混合 | 训练年化 | 验证年化 |", "| --- | --- | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['equity_grid']} | {row['mix']} | {row['train']['annual']:.2%} | {row['validation']['annual']:.2%} |")
    lines += ["", f"- 选中：`{selected['equity_grid']} / {selected['mix']}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 检验年化：{test_stats['annual']:.2%}", f"- 检验 Sharpe：{test_stats['sharpe']:.3f}", f"- 检验 MDD：{test_stats['mdd']:.2%}", f"- 近一年年化：{recent_stats['annual']:.2%}", f"- 检验 p05：{test_bootstrap['total_p05']:.2%}", f"- 近一年 p05：{recent_bootstrap['total_p05']:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
