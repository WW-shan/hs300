#!/usr/bin/env python3
"""R467: correction audit for degenerate R466 risk-budget variants."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r467_risk_budget_degeneracy_preregistration.json"
OUTPUT = E / "r467_risk_budget_degeneracy_2026-11-22.json"
RESULT = E / "r467_risk_budget_degeneracy_2026-11-22.md"
SOURCE = E / "r466_sleeve_risk_budget_mc_2026-11-22.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R467 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R467":
        raise RuntimeError("R467 round mismatch")
    if payload["selection"]["basis"] != "train_and_validation_only":
        raise RuntimeError("R467 selection basis changed")
    return payload


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def weight_path_is_degenerate(path: list[list[float]], *, max_weight: float) -> bool:
    """A path is degenerate if the cap fixes every sleeve to the same weight."""
    if not path:
        return True
    width = len(path[0])
    if width == 0 or max_weight <= 1.0 / width + 1e-12:
        return True
    rounded = {tuple(round(float(value), 10) for value in row) for row in path}
    return len(rounded) < 3


def _weight_path(curves: dict[str, list[dict[str, Any]]], variant: dict[str, Any], prereg: dict[str, Any]) -> list[list[float]]:
    r466 = load_module(ROOT / "scripts" / "research_r466_sleeve_risk_budget_mc.py", "r467_r466")
    names = list(variant["sleeve_names"])
    dates, returns = r466.align_curves(curves, names)
    lookback = int(variant["lookback"])
    rebalance = int(variant["rebalance"])
    weights = np.array([float(variant["weights"][name]) for name in names], dtype=float)
    weights = r466._cap_weights(weights, max_weight=float(variant["max_weight"]), min_weight=float(prereg["sleeve_overlay"]["min_weight"]))
    path: list[list[float]] = [weights.tolist()]
    for index in range(1, len(dates)):
        if index > 1 and (index - 1) % rebalance == 0:
            history = returns[max(0, index - 1 - lookback) : index - 1]
            weights = np.array(
                r466.estimate_weights(
                    history,
                    variant["method"],
                    max_weight=float(variant["max_weight"]),
                    min_weight=float(prereg["sleeve_overlay"]["min_weight"]),
                ),
                dtype=float,
            )
            path.append(weights.tolist())
    return path


def _bootstrap(curve: list[dict[str, Any]], prereg: dict[str, Any], r453: Any, offset: int) -> dict[str, float]:
    return r453.bootstrap(
        curve,
        block=int(prereg["bootstrap"]["block_days"]),
        paths=int(prereg["bootstrap"]["replications"]),
        seed=int(prereg["bootstrap"]["seed"]) + offset,
    )


def main() -> int:
    ensure_outputs_absent()
    prereg = load_prereg()
    r464 = load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r467_r464")
    r466 = load_module(ROOT / "scripts" / "research_r466_sleeve_risk_budget_mc.py", "r467_r466")
    r453 = load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r467_r453")
    p5 = r464.load_p5()
    panel, symbols = r464.build_panel(p5)
    service = p5.portfolio._service()
    curves_2x = r466.build_sleeve_curves(r464, p5, service, panel, symbols, r466.load_prereg(), cost_label="2x")
    variants = r466.generate_variants()
    windows = prereg["windows"]
    selected_curves: dict[str, list[dict[str, Any]]] = {}
    rows = []
    for variant in variants:
        curve = r466.overlay_curve(
            curves_2x,
            variant,
            min_weight=float(prereg["sleeve_overlay"]["min_weight"]),
            turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]),
        )
        selected_curves[variant["id"]] = curve
        path = _weight_path(curves_2x, variant, prereg)
        degenerate = weight_path_is_degenerate(path, max_weight=float(variant["max_weight"]))
        row = dict(variant)
        row["train"] = r466.path_metrics(r466.slice_curve(curve, windows["train_start"], windows["train_end"]))
        row["validation"] = r466.path_metrics(r466.slice_curve(curve, windows["validation_start"], windows["validation_end"]))
        row["distinct_weight_vectors"] = len({tuple(round(float(value), 10) for value in item) for item in path})
        row["degenerate_weight_path"] = degenerate
        row["training_gate"] = bool(
            not degenerate
            and row["train"]["annual"] >= prereg["selection"]["train_annual_min"]
            and row["validation"]["annual"] >= prereg["selection"]["validation_annual_min"]
            and row["validation"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
            and row["validation"]["mdd"] >= prereg["selection"]["validation_mdd_min"]
        )
        rows.append(row)
    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["validation"]["sharpe"]), reverse=True)
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for row in finalists:
        curve = selected_curves[row["id"]]
        row["validation_bootstrap"] = _bootstrap(r466.slice_curve(curve, windows["validation_start"], windows["validation_end"]), prereg, r453, 100)
    bootstrap_positive = [row for row in finalists if row["validation_bootstrap"]["total_p05"] > 0.0]
    neighborhood_finalists = bootstrap_positive[: int(prereg["selection"]["neighborhood_finalists"])]
    for row in neighborhood_finalists:
        local = [item for item in rows if item["universe"] == row["universe"] and item["method"] == row["method"] and not item["degenerate_weight_path"]]
        passed = sum(int(item["training_gate"]) for item in local)
        row["neighborhood"] = {"points": len(local), "pass_count": passed, "pass_rate": passed / len(local) if local else 0.0}
    stable = [row for row in neighborhood_finalists if row["neighborhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"]]
    stable.sort(key=lambda row: (min(row["train"]["annual"], row["validation"]["annual"]), row["validation_bootstrap"]["total_p05"]), reverse=True)
    selected = stable[0] if stable else (bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else rows[0]))
    selected_curve = selected_curves[selected["id"]]
    selected["test"] = r466.path_metrics(r466.slice_curve(selected_curve, windows["test_start"], windows["test_end"]))
    selected["recent"] = r466.path_metrics(r466.slice_curve(selected_curve, windows["recent_start"], windows["test_end"]))
    curves_3x = r466.build_sleeve_curves(r464, p5, service, panel, symbols, r466.load_prereg(), cost_label="3x", sleeve_names=list(selected["sleeve_names"]))
    stress_curve = r466.overlay_curve(curves_3x, selected, min_weight=float(prereg["sleeve_overlay"]["min_weight"]), turnover_cost_bps=float(prereg["sleeve_overlay"]["turnover_cost_bps"]))
    selected["test_3x"] = r466.path_metrics(r466.slice_curve(stress_curve, windows["test_start"], windows["test_end"]))
    selected["test_bootstrap"] = _bootstrap(r466.slice_curve(selected_curve, windows["test_start"], windows["test_end"]), prereg, r453, 1)
    selected["recent_bootstrap"] = _bootstrap(r466.slice_curve(selected_curve, windows["recent_start"], windows["test_end"]), prereg, r453, 2)
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "neighborhood_platform_pass": bool(selected.get("neighborhood")) and selected["neighborhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"],
        "test_annual_min": selected["test"]["annual"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["mdd"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["annual"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["annual"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "no_leverage": True,
    }
    payload = {
        "round": "R467",
        "source_round": "R466",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "source_sha256": sha(SOURCE),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "variant_count": len(rows),
        "degenerate_count": sum(row["degenerate_weight_path"] for row in rows),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "neighborhood_stable_count": len(stable),
        "rows": rows,
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R467 风险预算退化修正",
        "",
        "R466 中 4 个 sleeve、25% 权重上限会让四种风险预算方法退化成等权；本轮保留原证据，排除这种退化样本后重新确认。",
        "",
        f"- 原样本：{len(rows)}",
        f"- 退化样本：{payload['degenerate_count']}",
        f"- 非退化且训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 邻域稳定：{len(stable)}",
        f"- 选中：`{selected['id']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['annual']:.2%}",
        f"- 检验 Sharpe：{selected['test']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['mdd']:.2%}",
        f"- 近一年年化：{selected['recent']['annual']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 选中规则", "", f"- universe：{selected['universe']}", f"- method：{selected['method']}", f"- lookback：{selected['lookback']}", f"- rebalance：{selected['rebalance']}", f"- max_weight：{selected['max_weight']:.2f}", f"- 不同权重向量：{selected['distinct_weight_vectors']}", f"- 3× 检验年化：{selected['test_3x']['annual']:.2%}", "", "## 边界", ""]
    lines += [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
