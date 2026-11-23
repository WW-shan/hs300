#!/usr/bin/env python3
"""R419: confirm R418's four admitted factors as a compact portfolio."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import require_formal_evidence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r419_admitted_four_factor_portfolio_preregistration.json"
OUTPUT = E / "r419_admitted_four_factor_portfolio_2026-11-22.json"
RESULT = E / "r419_admitted_four_factor_portfolio_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r419_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prereg() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R419":
        raise RuntimeError("R419 round mismatch")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R419 preregistration checksum mismatch")
    return payload


def attach_60d(panel: pl.DataFrame) -> pl.DataFrame:
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    extra = load_existing_ext_factors(
        DATA,
        {"ext_alt_hs300_vol_of_vol_60d_low": "vol_of_vol_60d_low"},
    )
    return panel.join(extra, on=["symbol", "date"], how="left")


def run(p5: Any, service: Any, prepared: Any, members: list[str], directions: dict[str, str], *, start: str, end: str, cost: str, max_positions: int, max_hold_days: int, label: str) -> dict[str, Any]:
    del prepared
    scoring, selected = p5._equal_inputs(members, directions)
    config = p5.portfolio._backtest_config(
        scoring,
        selected,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=max_positions,
        cost=p5.portfolio._cost_parameters(cost),
        max_hold_days=max_hold_days,
    )
    result = service.run_with_evidence(config)
    require_formal_evidence(result, label="research_r419_admitted_four_factor_portfolio.py")
    if result.error:
        raise RuntimeError(f"{label}: {result.error}")
    stats = result.stats
    return {
        "annual_return": stats["annual_return"],
        "total_return": stats["total_return"],
        "sharpe": stats["sharpe"],
        "max_drawdown": stats["max_drawdown"],
        "n_trades": stats["n_trades"],
    }

def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R419 output exists: {path.name}")
    prereg = load_prereg()
    p5 = load_p5()
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    panel = attach_60d(panel)
    missing = [f for f in prereg["admitted_members"] if f not in panel.columns]
    if missing:
        raise RuntimeError(f"R419 panel missing admitted members: {missing}")
    service = p5.portfolio._service()
    symbols = sorted(panel["symbol"].unique().to_list())
    directions = prereg["directions"]
    windows = prereg["windows"]
    prepared_train = p5._prepare_shared_window(service, symbols, panel, prereg["admitted_members"], start=date.fromisoformat(windows["train_start"]), end=date.fromisoformat(windows["train_end"]))
    prepared_validation = p5._prepare_shared_window(service, symbols, panel, prereg["admitted_members"], start=date.fromisoformat(windows["validation_start"]), end=date.fromisoformat(windows["validation_end"]))
    prepared_test = p5._prepare_shared_window(service, symbols, panel, prereg["admitted_members"], start=date.fromisoformat(windows["test_start"]), end=date.fromisoformat(windows["test_end"]))
    rows = []
    for positions in prereg["grid"]["max_positions"]:
        for hold in prereg["grid"]["max_hold_days"]:
            row = {
                "max_positions": positions,
                "max_hold_days": hold,
                "members": prereg["admitted_members"],
                "train_2x": run(p5, service, prepared_train, prereg["admitted_members"], directions, start=windows["train_start"], end=windows["train_end"], cost="2x", max_positions=positions, max_hold_days=hold, label=f"r419_{positions}_{hold}_train"),
                "validation_2x": run(p5, service, prepared_validation, prereg["admitted_members"], directions, start=windows["validation_start"], end=windows["validation_end"], cost="2x", max_positions=positions, max_hold_days=hold, label=f"r419_{positions}_{hold}_validation"),
            }
            rows.append(row)
    for row in rows:
        row["test_2x"] = run(p5, service, prepared_test, prereg["admitted_members"], directions, start=windows["test_start"], end=windows["test_end"], cost="2x", max_positions=row["max_positions"], max_hold_days=row["max_hold_days"], label=f"r419_{row['max_positions']}_{row['max_hold_days']}_test")
        row["test_3x"] = run(p5, service, prepared_test, prereg["admitted_members"], directions, start=windows["test_start"], end=windows["test_end"], cost="3x", max_positions=row["max_positions"], max_hold_days=row["max_hold_days"], label=f"r419_{row['max_positions']}_{row['max_hold_days']}_test_3x")
    eligible = [row for row in rows if row["train_2x"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation_2x"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    eligible.sort(key=lambda row: (min(row["train_2x"]["annual_return"], row["validation_2x"]["annual_return"]), row["max_positions"], row["max_hold_days"]), reverse=True)
    selected = eligible[0] if eligible else max(rows, key=lambda row: min(row["train_2x"]["annual_return"], row["validation_2x"]["annual_return"]))
    gates = prereg["gates"]
    checks = {
        "has_platform": len(eligible) >= prereg["selection"]["platform_min_points"],
        "train_annual_min": selected["train_2x"]["annual_return"] >= gates["train_annual_min"],
        "validation_annual_min": selected["validation_2x"]["annual_return"] >= gates["validation_annual_min"],
        "test_annual_min": selected["test_2x"]["annual_return"] >= gates["test_annual_min"],
        "test_sharpe_min": selected["test_2x"]["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": selected["test_2x"]["max_drawdown"] >= gates["test_mdd_min"],
        "stress_3x_positive": selected["test_3x"]["annual_return"] > 0,
        "recent_positive": selected["test_2x"]["annual_return"] > 0,
    }
    payload = {
        "round": "R419",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "admitted_members": prereg["admitted_members"],
        "grid": rows,
        "selected": selected,
        "eligible_count": len(eligible),
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R419 四因子准入组合确认",
        "",
        "| 仓数 | 持有 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(f"| {row['max_positions']} | {row['max_hold_days']} | {row['train_2x']['annual_return']:.2%} | {row['validation_2x']['annual_return']:.2%} | {row['test_2x']['annual_return']:.2%} | {row['test_2x']['sharpe']:.3f} | {row['test_2x']['max_drawdown']:.2%} | {row['test_3x']['annual_return']:.2%} |")
    lines += ["", f"- 选中：{selected['max_positions']} 仓 / {selected['max_hold_days']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
