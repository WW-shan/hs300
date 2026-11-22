#!/usr/bin/env python3
"""R453: portfolio confirmation of the R452 value_quality composite."""
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
PREREG = E / "r453_value_quality_portfolio_preregistration.json"
OUTPUT = E / "r453_value_quality_portfolio_2026-11-22.json"
RESULT = E / "r453_value_quality_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r453_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bootstrap(curve: list[dict[str, Any]], *, block: int, paths: int, seed: int) -> dict[str, float]:
    values = [float(curve[i]["value"]) / float(curve[i - 1]["value"]) - 1.0 for i in range(1, len(curve))]
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
        mean = sum(selected) / len(selected)
        vol = (sum((item - mean) ** 2 for item in selected) / len(selected)) ** 0.5
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


def run_variant(
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    scoring: dict[str, float],
    directions: dict[str, str],
    *,
    start: str,
    end: str,
    positions: int,
    hold: int,
    cost_label: str,
) -> dict[str, Any]:
    # Build a market matrix without the synthetic field first, then attach the
    # daily rank composite. Feature resolution must not depend on a catalog name.
    seed = next(iter(scoring))
    seed_config = p5.portfolio._backtest_config(
        {seed: 1.0},
        {seed: "high"},
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=20,
        cost=p5.portfolio._cost_parameters("2x"),
        max_hold_days=hold,
    )
    seed_config.symbols = list(symbols)
    prepared_base = service.prepare_matrix_optimization([seed_config])
    prepared = p5._extend_prepared_with_panel(prepared_base, panel, list(scoring))
    cost = p5.portfolio._cost_parameters(cost_label) if cost_label != "5x" else {
        "fees_pct": 0.001,
        "commission_pct": 0.001,
        "stamp_tax_pct": 0.0005,
        "slippage_bps": 25.0,
    }
    config = p5.portfolio._backtest_config(scoring, {factor: directions[factor] for factor in scoring}, start=date.fromisoformat(start), end=date.fromisoformat(end), max_positions=positions, cost=cost, max_hold_days=hold)
    config.symbols = list(prepared.market_data.symbols)
    result = service.run(config, prepared=prepared)
    if result.error:
        raise RuntimeError(result.error)
    return {"stats": p5._select_stats(result.stats), "curve": list(result.equity_curve), "trades": len(result.trades)}


def build_panel(p5: Any, prereg: dict[str, Any]) -> tuple[pl.DataFrame, list[str]]:
    from app.backtest.fundamentals import attach_fundamental_factors, load_fundamental_snapshot
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.factor_research.incremental import rank_score_frame
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    snapshot = load_fundamental_snapshot(DATA)
    if snapshot is None:
        raise RuntimeError("financial snapshot unavailable")
    panel = attach_fundamental_factors(panel, snapshot, ["pb_latest", "ocf_debt", "debt_ratio_latest"])
    panel = panel.join(
        load_existing_ext_factors(
            DATA,
            {
                "ext_alt_hs300_cash_flow_stability_4q_low": "cash_flow_stability_4q_low",
                "ext_alt_hs300_dividend_yield": "dividend_yield",
                "ext_alt_hs300_fund_holding_ratio_low": "fund_holding_ratio_low",
                "ext_alt_hs300_idio_vol_40d_low": "idio_vol_40d_low",
                "ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low",
            },
        ),
        on=["symbol", "date"],
        how="left",
    ).sort(["symbol", "date"])
    for member in ("pb_latest", "ocf_debt", "debt_ratio_latest"):
        panel = rank_score_frame(panel, member, "low" if member != "ocf_debt" else "high", output_column=f"_vq_{member}")
    panel = panel.with_columns(
        (sum(pl.col(f"_vq_{member}").fill_null(0.0) for member in ("pb_latest", "ocf_debt", "debt_ratio_latest")) / 3.0).alias("value_quality"),
        sum(pl.col(f"_vq_{member}").is_not_null().cast(pl.Int8) for member in ("pb_latest", "ocf_debt", "debt_ratio_latest")).alias("_vq_coverage"),
    ).filter(pl.col("_vq_coverage") == 3)
    return panel, sorted(panel["symbol"].unique().to_list())


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R453 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R453 preregistration checksum mismatch")
    p5 = load_p5()
    panel, symbols = build_panel(p5, prereg)
    service = p5.portfolio._service()
    directions = dict(prereg["controls"]["directions"])
    directions["value_quality"] = "high"
    controls = {
        "four": list(prereg["controls"]["four"]),
        "five": list(prereg["controls"]["five"]),
    }
    scoring = {}
    for name, members in controls.items():
        scoring[name] = {member: 1.0 / len(members) for member in members}
    scoring["value_quality"] = {"value_quality": 1.0}
    variants = {
        "four": scoring["four"],
        "five": scoring["five"],
        "value_quality_only": scoring["value_quality"],
        "four_plus_value_quality_20": {**{k: v * 0.8 for k, v in scoring["four"].items()}, "value_quality": 0.2},
        "five_plus_value_quality_20": {**{k: v * 0.8 for k, v in scoring["five"].items()}, "value_quality": 0.2},
        "four_plus_value_quality_10": {**{k: v * 0.9 for k, v in scoring["four"].items()}, "value_quality": 0.1},
    }
    windows = prereg["windows"]
    rows = []
    for name, weights in variants.items():
        row = {"name": name, "weights": weights, "train": {}, "validation": {}}
        for window in ("train", "validation"):
            row[window] = run_variant(p5, service, panel, symbols, weights, directions, start=windows[f"{window}_start"], end=windows[f"{window}_end"], positions=20, hold=30, cost_label="2x")
        rows.append(row)
    # Use only train+validation to choose the added variant and compare position grids.
    grid = []
    for positions in prereg["grid"]["max_positions"]:
        for hold in prereg["grid"]["max_hold_days"]:
            for name in ("four_plus_value_quality_20", "four_plus_value_quality_10"):
                row = {"name": name, "positions": positions, "hold": hold}
                for window in ("train", "validation"):
                    row[window] = run_variant(p5, service, panel, symbols, variants[name], directions, start=windows[f"{window}_start"], end=windows[f"{window}_end"], positions=positions, hold=hold, cost_label="2x")
                grid.append(row)
    eligible = [row for row in grid if row["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["positions"], row["hold"], row["name"]), reverse=True)
    selected = eligible[0] if eligible else max(grid, key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]))
    selected_weights = variants[selected["name"]]
    # Test, stress and recent are confirmation only.
    test = run_variant(p5, service, panel, symbols, selected_weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    stress = run_variant(p5, service, panel, symbols, selected_weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="3x")
    diagnostic = run_variant(p5, service, panel, symbols, selected_weights, directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="5x")
    recent = run_variant(p5, service, panel, symbols, selected_weights, directions, start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    four_test = run_variant(p5, service, panel, symbols, scoring["four"], directions, start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    four_recent = run_variant(p5, service, panel, symbols, scoring["four"], directions, start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    selected["test"] = test
    selected["test_3x"] = stress
    selected["test_5x"] = diagnostic
    selected["recent"] = recent
    selected["test_bootstrap"] = bootstrap(test["curve"], block=20, paths=5000, seed=453)
    selected["recent_bootstrap"] = bootstrap(recent["curve"], block=20, paths=5000, seed=454)
    increment_test = selected["test"]["stats"]["annual_return"] - four_test["stats"]["annual_return"]
    increment_recent = selected["recent"]["stats"]["annual_return"] - four_recent["stats"]["annual_return"]
    checks = {
        "train_annual_min": selected["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0,
        "increment_vs_four_test_positive": increment_test > 0,
        "increment_vs_four_recent_positive": increment_recent > 0,
        "no_leverage": True,
    }
    payload = {
        "round": "R453",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "panel_symbols": len(symbols),
        "control_rows": [
            {**row, **{window: {k: v for k, v in row[window].items() if k != "curve"} for window in ("train", "validation")}}
            for row in rows
        ],
        "grid": [
            {**row, **{window: {k: v for k, v in row[window].items() if k != "curve"} for window in ("train", "validation")}}
            for row in grid
        ],
        "selected": {**selected, "test": {k: v for k, v in test.items() if k != "curve"}, "test_3x": {k: v for k, v in stress.items() if k != "curve"}, "test_5x": {k: v for k, v in diagnostic.items() if k != "curve"}, "recent": {k: v for k, v in recent.items() if k != "curve"}},
        "increment_vs_four": {"test_annual": increment_test, "recent_annual": increment_recent},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "five_x_hard_rejection": False, "five_x_stats": diagnostic["stats"]},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R453 财报质量复合因子组合验证", "", "| 变体 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 近一年年化 | 3×检验年化 | 检验p05 | 近一年p05 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['name']} | {row['train']['stats']['annual_return']:.2%} | {row['validation']['stats']['annual_return']:.2%} | - | - | - | - | - | - | - |")
    lines += ["", f"- 选中：`{selected['name']}`，{selected['positions']} 仓 / {selected['hold']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 相对四因子：检验年化 {increment_test:.2%}；近一年 {increment_recent:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 选中结果", "", f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}", f"- 检验 Sharpe：{selected['test']['stats']['sharpe']:.3f}", f"- 检验 MDD：{selected['test']['stats']['max_drawdown']:.2%}", f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}", f"- 近一年年化：{selected['recent']['stats']['annual_return']:.2%}", f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
