#!/usr/bin/env python3
"""R396: frozen neighbourhood audit of the R395 selected factor weights."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r396_weight_stability_preregistration.json"
R395 = EVIDENCE / "r395_factor_weight_mc_2026-11-22.json"
OUTPUT = EVIDENCE / "r396_weight_stability_2026-11-22.json"
RESULT = EVIDENCE / "r396_weight_stability_2026-11-22.md"
FACTOR_IDS = (
    "ext_alt_hs300_cash_flow_stability_4q_low",
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_holder_top10_share_chg_yoy",
    "ext_alt_hs300_idio_vol_40d_low",
    "ext_alt_hs300_margin_chg_1m_low",
    "ext_alt_hs300_ocf_margin_4q",
    "ext_alt_hs300_vol_of_vol_20d_low",
)
TRAIN = ("2022-05-05", "2025-05-05")
TEST = ("2025-05-06", "2026-11-05")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R396 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R396":
        raise RuntimeError("R396 round mismatch")
    if payload["source_r395_sha256"] != sha256(R395):
        raise RuntimeError("R395 evidence changed")
    return payload


def weights_within_limit(weights: dict[str, float]) -> bool:
    return (
        abs(sum(weights.values()) - 1.0) < 1e-9
        and all(value > 0.0 for value in weights.values())
    )


def _renormalise(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    return {factor: float(weights[factor]) / total for factor in weights}


def neighbourhood() -> list[dict[str, Any]]:
    prereg = load_prereg()
    base = {factor: float(prereg["selected_weights"][factor]) for factor in FACTOR_IDS}
    perturbation = float(prereg["neighbourhood"]["relative_weight_perturbation"])
    rows: list[dict[str, Any]] = [{"label": "selected", "kind": "selected", "weights": dict(base)}]
    for factor in FACTOR_IDS:
        for direction, multiplier in (("up", 1.0 + perturbation), ("down", 1.0 - perturbation)):
            raw = dict(base)
            raw[factor] = max(1e-6, raw[factor] * multiplier)
            rows.append({"label": f"{factor}_{direction}", "kind": "relative_weight", "weights": _renormalise(raw)})
    for factor in FACTOR_IDS:
        raw = {key: value for key, value in base.items() if key != factor}
        rows.append({
            "label": f"leave_out_{factor}",
            "kind": "leave_one_out",
            "weights": _renormalise(raw),
            "dropped_factor": factor,
        })
    return rows


def _portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r396_portfolio_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stats(module: Any, service: Any, weights: dict[str, float], *, start: str, end: str, cost: str) -> dict[str, Any]:
    from datetime import date
    record = module._run(
        service,
        weights,
        {factor: "high" for factor in weights},
        label=f"r396_{start}_{end}_{cost}",
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        max_positions=20,
        cost_label=cost,
    )
    s = record["stats"]
    return {"annual": s.get("annual_return"), "sharpe": s.get("sharpe"), "mdd": s.get("max_drawdown"), "total_return": s.get("total_return"), "trades": record["trade_count"]}


def _gate(metrics: dict[str, Any], baseline: dict[str, Any], prereg: dict[str, Any]) -> tuple[bool, list[str]]:
    gate = prereg["stability_gate"]
    reasons = []
    if metrics["annual"] < float(gate["minimum_test_annual"]):
        reasons.append("test_annual_below_floor")
    if metrics["sharpe"] < float(gate["minimum_test_sharpe"]):
        reasons.append("test_sharpe_below_floor")
    if baseline["annual"] - metrics["annual"] > float(gate["maximum_test_annual_drop"]):
        reasons.append("test_annual_drop_too_large")
    if baseline["sharpe"] - metrics["sharpe"] > float(gate["maximum_test_sharpe_drop"]):
        reasons.append("test_sharpe_drop_too_large")
    return not reasons, reasons


def build_report() -> dict[str, Any]:
    prereg = load_prereg()
    # R395 test result is bound by sidecar and serves as the audit baseline.
    r395 = json.loads(R395.read_text(encoding="utf-8"))
    base_train = r395["selected"]["train_2x"]
    base_test = r395["selected"]["test_2x"]
    base_3x = r395["selected"]["test_3x"]
    module = _portfolio_module()
    service = module._service()
    rows = []
    for row in neighbourhood():
        train = _stats(module, service, row["weights"], start=TRAIN[0], end=TRAIN[1], cost="2x")
        test = _stats(module, service, row["weights"], start=TEST[0], end=TEST[1], cost="2x")
        stress = _stats(module, service, row["weights"], start=TEST[0], end=TEST[1], cost="3x")
        passed, reasons = _gate(test, base_test, prereg)
        rows.append({**row, "train_2x": train, "test_2x": test, "test_3x": stress, "pass": passed, "failure_reasons": reasons})
        print(json.dumps({"label": row["label"], "train": train["annual"], "test": test["annual"], "sharpe": test["sharpe"], "pass": passed}), flush=True)
    pass_count = sum(row["pass"] for row in rows)
    return {
        "round": "R396",
        "classification": prereg["classification"],
        "runner_sha256": sha256(Path(__file__)),
        "preregistration_sha256": sha256(PREREG),
        "source_r395_sha256": sha256(R395),
        "baseline": {"train_2x": base_train, "test_2x": base_test, "test_3x": base_3x},
        "neighbourhood_count": len(rows),
        "pass_count": pass_count,
        "pass_rate": pass_count / len(rows),
        "rows": rows,
        "decision": {
            "status": "weight_stability_complete_no_promotion",
            "stable_weight_region": pass_count / len(rows) >= float(prereg["stability_gate"]["minimum_pass_rate"]),
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    base = report["baseline"]["test_2x"]
    lines = [
        "# R396 权重稳定性审计",
        "",
        "不重新选权，只检查 R395 入选权重在 ±20% 单腿扰动和留一因子扰动下是否连续。",
        "",
        f"- 邻域数：{report['neighbourhood_count']}",
        f"- 通过数：{report['pass_count']}（{report['pass_rate']:.1%}）",
        f"- 稳定权重区间：{'是' if report['decision']['stable_weight_region'] else '否'}",
        f"- R395 基线检验年化：{base['annual']:.2%} / Sharpe {base['sharpe']:.3f}",
        "",
        "## 邻域结果",
        "",
        "| 扰动 | 训练年化 | 检验年化 | 检验 Sharpe | 检验 MDD | 3× 检验年化 | 通过 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for row in report["rows"]:
        lines.append(f"| `{row['label']}` | {row['train_2x']['annual']:.2%} | {row['test_2x']['annual']:.2%} | {row['test_2x']['sharpe']:.3f} | {row['test_2x']['mdd']:.2%} | {row['test_3x']['annual']:.2%} | {'是' if row['pass'] else '否'} |")
    lines += ["", "## 结论边界", "", "- 这是冻结后敏感性审计，不重新选择参数。", "- 不生成订单、纸面目标或生产配置。", ""]
    return "\n".join(lines)


def main() -> int:
    ensure_outputs_absent()
    report = build_report()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    md = markdown(report)
    RESULT.write_text(md, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
