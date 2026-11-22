#!/usr/bin/env python3
"""R456: compact PB + OCF/debt portfolio confirmation."""
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
PREREG = E / "r456_value_quality_compact_portfolio_preregistration.json"
OUTPUT = E / "r456_value_quality_compact_portfolio_2026-11-22.json"
RESULT = E / "r456_value_quality_compact_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r456_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[index]["value"]) / float(curve[index - 1]["value"]) - 1.0 for index in range(1, len(curve))]
    if not values:
        return {"total_p05": 0.0, "total_p50": 0.0, "sharpe_p50": 0.0, "mdd_p05": 0.0}
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


def extend_panel(p5: Any) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, ["pb_latest", "ocf_debt"])
    panel = panel.join(
        load_existing_ext_factors(DATA, {
            "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
            "ext_alt_hs300_dividend_yield": "dividend_yield",
            "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
            "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
        }),
        on=["symbol", "date"], how="left",
    ).sort(["symbol", "date"])
    return panel, sorted(panel["symbol"].unique().to_list())


def run(p5: Any, service: Any, panel: pl.DataFrame, symbols: list[str], scoring: dict[str, float], directions: dict[str, str], *, start: str, end: str, positions: int, hold: int, cost_label: str) -> dict[str, Any]:
    seed = next(iter(scoring))
    seed_config = p5.portfolio._backtest_config({seed: 1.0}, {seed: "high"}, start=date.fromisoformat(start), end=date.fromisoformat(end), max_positions=20, cost=p5.portfolio._cost_parameters("2x"), max_hold_days=hold)
    seed_config.symbols = list(symbols)
    base = service.prepare_matrix_optimization([seed_config])
    prepared = p5._extend_prepared_with_panel(base, panel, list(scoring))
    cost = p5.portfolio._cost_parameters(cost_label) if cost_label != "5x" else {"fees_pct": 0.001, "commission_pct": 0.001, "stamp_tax_pct": 0.0005, "slippage_bps": 25.0}
    config = p5.portfolio._backtest_config(scoring, {factor: directions[factor] for factor in scoring}, start=date.fromisoformat(start), end=date.fromisoformat(end), max_positions=positions, cost=cost, max_hold_days=hold)
    config.symbols = list(prepared.market_data.symbols)
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": p5._select_stats(result.stats), "curve": list(result.equity_curve), "trades": len(result.trades)}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R456 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R456 preregistration checksum mismatch")
    p5 = load_p5()
    panel, symbols = extend_panel(p5)
    service = p5.portfolio._service()
    directions = {"pb_latest": "low", "ocf_debt": "high", "ext_alt_hs300_cash_flow_stability_4q_low": "high", "ext_alt_hs300_dividend_yield": "high", "ext_alt_hs300_fund_holding_ratio_low": "high", "ext_alt_hs300_idio_vol_40d_low": "high"}
    four = list(prereg["controls"]["four_dual_admitted"])
    compact = {"pb_latest": 0.6, "ocf_debt": 0.4}
    variants = {
        "compact_only": compact,
        "four": {factor: 0.25 for factor in four},
        "four_plus_compact_10": {**{factor: 0.225 for factor in four}, **{factor: 0.1 * weight for factor, weight in compact.items()}},
        "four_plus_compact_20": {**{factor: 0.2 for factor in four}, **{factor: 0.2 * weight for factor, weight in compact.items()}},
    }
    windows = prereg["windows"]
    controls = {}
    for name in ("compact_only", "four", "four_plus_compact_10", "four_plus_compact_20"):
        controls[name] = {}
        for window in ("train", "validation"):
            controls[name][window] = run(p5, service, panel, symbols, variants[name], directions, start=windows[f"{window}_start"], end=windows[f"{window}_end"], positions=20, hold=30, cost_label="2x")
    grid = []
    for positions in prereg["grid"]["max_positions"]:
        for hold in prereg["grid"]["max_hold_days"]:
            for name in ("four_plus_compact_10", "four_plus_compact_20"):
                row = {"name": name, "positions": positions, "hold": hold}
                for window in ("train", "validation"):
                    row[window] = run(p5, service, panel, symbols, variants[name], directions, start=windows[f"{window}_start"], end=windows[f"{window}_end"], positions=positions, hold=hold, cost_label="2x")
                grid.append(row)
    eligible = [row for row in grid if row["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["positions"], row["hold"], row["name"]), reverse=True)
    selected = eligible[0] if eligible else max(grid, key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]))
    selected_weights = variants[selected["name"]]
    test = run(p5, service, panel, symbols, selected_weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    stress = run(p5, service, panel, symbols, selected_weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="3x")
    recent = run(p5, service, panel, symbols, selected_weights, directions, start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    four_test = run(p5, service, panel, symbols, variants["four"], directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    four_recent = run(p5, service, panel, symbols, variants["four"], directions, start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    test_bootstrap = bootstrap(test["curve"], block=20, paths=5000, seed=456)
    recent_bootstrap = bootstrap(recent["curve"], block=20, paths=5000, seed=457)
    increment_test = test["stats"]["annual_return"] - four_test["stats"]["annual_return"]
    increment_recent = recent["stats"]["annual_return"] - four_recent["stats"]["annual_return"]
    checks = {
        "train_annual_min": selected["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": test["stats"]["annual_return"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": test["stats"]["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": test["stats"]["max_drawdown"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": recent["stats"]["annual_return"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": stress["stats"]["annual_return"] > 0,
        "test_total_p05_positive": test_bootstrap["total_p05"] > 0,
        "recent_total_p05_positive": recent_bootstrap["total_p05"] > 0,
        "increment_vs_four_test_positive": increment_test > 0,
        "increment_vs_four_recent_positive": increment_recent > 0,
        "no_leverage": True,
    }
    payload = {"round": "R456", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "controls": controls, "grid": [{**row, "train": {"stats": row["train"]["stats"], "trades": row["train"]["trades"]}, "validation": {"stats": row["validation"]["stats"], "trades": row["validation"]["trades"]}} for row in grid], "selected": {**selected, "test": {"stats": test["stats"], "trades": test["trades"]}, "test_3x": {"stats": stress["stats"], "trades": stress["trades"]}, "recent": {"stats": recent["stats"], "trades": recent["trades"]}, "test_bootstrap": test_bootstrap, "recent_bootstrap": recent_bootstrap}, "increment_vs_four": {"test_annual": increment_test, "recent_annual": increment_recent}, "gate": {"pass": all(checks.values()), "checks": checks}, "diagnostics": {"five_x_warning_only": True}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R456 PB+OCF 紧凑组合验证", "", "| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, windows_result in controls.items():
        lines.append(f"| {name} | {windows_result['train']['stats']['annual_return']:.2%} | {windows_result['validation']['stats']['annual_return']:.2%} | - | - | - | - | - | - |")
    lines += ["", f"- 选中：`{selected['name']}`，{selected['positions']} 仓 / {selected['hold']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 检验年化：{test['stats']['annual_return']:.2%}", f"- 检验 Sharpe：{test['stats']['sharpe']:.3f}", f"- 检验 MDD：{test['stats']['max_drawdown']:.2%}", f"- 近一年年化：{recent['stats']['annual_return']:.2%}", f"- 检验 p05：{test_bootstrap['total_p05']:.2%}", f"- 近一年 p05：{recent_bootstrap['total_p05']:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
