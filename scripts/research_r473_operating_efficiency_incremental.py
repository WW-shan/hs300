#!/usr/bin/env python3
"""R473: incremental sleeve test of R468 operating efficiency against R463 four-factor control."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r473_operating_efficiency_incremental_preregistration.json"
OUTPUT = E / "r473_operating_efficiency_incremental_2026-11-22.json"
RESULT = E / "r473_operating_efficiency_incremental_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R473 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_samples() -> list[dict[str, Any]]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    four = list(prereg["four_factor"])
    op = str(prereg["op_factor"])
    rows = []
    for op_weight in prereg["weight_grid"]:
        four_weight = 1.0 - float(op_weight)
        rows.append(
            {
                "label": f"four_plus_op_{round(float(op_weight) * 100)}",
                "op_weight": float(op_weight),
                "weights": {**{factor: four_weight / len(four) for factor in four}, op: float(op_weight)},
            }
        )
    return rows


def run_combo(
    r464: Any,
    p5: Any,
    service: Any,
    panel: pl.DataFrame,
    symbols: list[str],
    weights: dict[str, float],
    *,
    start: str,
    end: str,
    cost_label: str,
    op_execution: dict[str, Any],
    four_execution: dict[str, Any],
) -> dict[str, Any]:
    # The matrix strategy accepts one common max-hold parameter. Use the
    # operating-efficiency hold for the combined sleeve so the incremental
    # sleeve is not silently forced to the four-factor hold.
    sample = {
        "weights": weights,
        "max_positions": int(op_execution["max_positions"]),
        "max_hold_days": int(op_execution["max_hold_days"]),
        "max_exposure_pct": 1.0,
        "entry_score": float(op_execution["entry_score"]),
        "exit_score": float(op_execution["exit_score"]),
    }
    return r464._run_config(
        p5,
        service,
        panel,
        symbols,
        sample,
        start=start,
        end=end,
        cost_label=cost_label,
    )


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r473_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r473_r464")
    r453 = r468.load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r473_r453")
    p5 = r464.load_p5()
    panel, symbols, coverage = r468.build_panel(p5)
    # The four-factor columns come from R464's panel. The operating-efficiency
    # columns are already attached by R468's build_panel; join the remaining
    # core columns from R464 and de-duplicate.
    panel4, symbols4 = r464.build_panel(p5)
    panel = panel.join(panel4.select(["symbol", "date", *prereg["four_factor"]]), on=["symbol", "date"], how="left")
    symbols = sorted(set(symbols) | set(symbols4))
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    for row in rows:
        row["train"] = run_combo(r464, p5, service, panel, symbols, row["weights"], start=windows["train_start"], end=windows["train_end"], cost_label="2x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
        row["validation"] = run_combo(r464, p5, service, panel, symbols, row["weights"], start=windows["validation_start"], end=windows["validation_end"], cost_label="2x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
        row["training_gate"] = bool(
            row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
            and row["validation"]["stats"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
            and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
            and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        )
    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["validation"]["stats"]["sharpe"]), reverse=True)
    for row in eligible:
        row["validation_bootstrap"] = r453.bootstrap(row["validation"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 100)
    bootstrap_positive = [row for row in eligible if row["validation_bootstrap"]["total_p05"] > 0.0]
    selected = bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else rows[0])
    four_weights = {factor: 1.0 / len(prereg["four_factor"]) for factor in prereg["four_factor"]}
    control = {}
    for window in ("test", "recent"):
        start = windows[f"{window}_start"]
        end = windows["test_end"] if window == "recent" else windows[f"{window}_end"]
        control[window] = run_combo(r464, p5, service, panel, symbols, four_weights, start=start, end=end, cost_label="2x", op_execution=prereg["four_execution"], four_execution=prereg["four_execution"])
    selected["test"] = run_combo(r464, p5, service, panel, symbols, selected["weights"], start=windows["test_start"], end=windows["test_end"], cost_label="2x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
    selected["recent"] = run_combo(r464, p5, service, panel, symbols, selected["weights"], start=windows["recent_start"], end=windows["test_end"], cost_label="2x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
    selected["test_3x"] = run_combo(r464, p5, service, panel, symbols, selected["weights"], start=windows["test_start"], end=windows["test_end"], cost_label="3x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
    selected["test_5x"] = run_combo(r464, p5, service, panel, symbols, selected["weights"], start=windows["test_start"], end=windows["test_end"], cost_label="5x", op_execution=prereg["op_execution"], four_execution=prereg["four_execution"])
    selected["test_bootstrap"] = r453.bootstrap(selected["test"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 1)
    selected["recent_bootstrap"] = r453.bootstrap(selected["recent"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 2)
    increment_test = selected["test"]["stats"]["annual_return"] - control["test"]["stats"]["annual_return"]
    increment_recent = selected["recent"]["stats"]["annual_return"] - control["recent"]["stats"]["annual_return"]
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "increment_vs_four_test_positive": increment_test > 0.0,
        "increment_vs_four_recent_positive": increment_recent > 0.0,
        "no_leverage": True,
    }
    payload = {
        "round": "R473",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "coverage": coverage,
        "sample_count": len(rows),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "rows": [{**row, "train": {k: v for k, v in row["train"].items() if k != "curve"}, "validation": {k: v for k, v in row["validation"].items() if k != "curve"}} for row in rows],
        "control": {key: {k: v for k, v in value.items() if k != "curve"} for key, value in control.items()},
        "selected": {**selected, "test": {k: v for k, v in selected["test"].items() if k != "curve"}, "recent": {k: v for k, v in selected["recent"].items() if k != "curve"}, "test_3x": {k: v for k, v in selected["test_3x"].items() if k != "curve"}, "test_5x": {k: v for k, v in selected["test_5x"].items() if k != "curve"}},
        "increment_vs_four": {"test_annual": increment_test, "recent_annual": increment_recent},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R473 营运效率增量 sleeve 验证",
        "",
        "本轮把 R468 的营运效率因子作为 sleeve 加入 R463 四因子基线，只改变营运效率权重，训练+验证选样，检验+近一年确认。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 选中：`{selected['label']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}",
        f"- 检验 Sharpe：{selected['test']['stats']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['stats']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['stats']['annual_return']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        f"- 相对四因子检验增量：{increment_test:.2%}",
        f"- 相对四因子近一年增量：{increment_recent:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
