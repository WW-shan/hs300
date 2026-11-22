#!/usr/bin/env python3
"""R470: standalone confirmation of the value_quality_safety sleeve."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r470_value_quality_safety_standalone_preregistration.json"
OUTPUT = E / "r470_value_quality_safety_standalone_2026-11-22.json"
RESULT = E / "r470_value_quality_safety_standalone_2026-11-22.md"
FACTOR = "value_quality_safety"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R470 output exists; refusing overwrite: {path.name}")


def load_prereg() -> dict[str, Any]:
    verify_sidecar(PREREG)
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R470":
        raise RuntimeError("R470 round mismatch")
    if payload["selection"]["basis"] != "train_and_validation_only":
        raise RuntimeError("R470 selection basis changed")
    return payload


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_samples() -> list[dict[str, Any]]:
    prereg = load_prereg()
    rows = []
    for positions in prereg["parameter_space"]["max_positions"]:
        for hold in prereg["parameter_space"]["max_hold_days"]:
            for entry in prereg["parameter_space"]["entry_score"]:
                for exit_ in prereg["parameter_space"]["exit_score"]:
                    rows.append(
                        {
                            "label": f"vqs|p{positions}|h{hold}|e{int(entry)}|x{int(exit_)}",
                            "factor": FACTOR,
                            "direction": "high",
                            "weights": {FACTOR: 1.0},
                            "max_positions": positions,
                            "max_hold_days": hold,
                            "entry_score": entry,
                            "exit_score": exit_,
                            "max_exposure_pct": 1.0,
                        }
                    )
    return rows


def _training_gate(row: dict[str, Any], prereg: dict[str, Any]) -> bool:
    train = row["train"]["stats"]
    validation = row["validation"]["stats"]
    gate = prereg["selection"]
    return bool(
        train["annual_return"] >= gate["train_annual_min"]
        and validation["annual_return"] >= gate["validation_annual_min"]
        and validation["sharpe"] >= gate["validation_sharpe_min"]
        and validation["max_drawdown"] >= gate["validation_mdd_min"]
    )


def _neighborhood(row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    local = [item for item in rows if abs(int(item["max_hold_days"]) - int(row["max_hold_days"])) <= 10 and abs(int(item["max_positions"]) - int(row["max_positions"])) <= 10]
    passed = sum(int(item["training_gate"]) for item in local)
    return {"points": len(local), "pass_count": passed, "pass_rate": passed / len(local) if local else 0.0}


def main() -> int:
    ensure_outputs_absent()
    prereg = load_prereg()
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r470_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r470_r464")
    r453 = r468.load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r470_r453")
    p5 = r464.load_p5()
    panel, symbols = r464.build_panel(p5)
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    for row in rows:
        sample = {
            "weights": row["weights"],
            "direction": row["direction"],
            "max_positions": int(row["max_positions"]),
            "max_hold_days": int(row["max_hold_days"]),
            "max_exposure_pct": 1.0,
            "entry_score": float(row["entry_score"]),
            "exit_score": float(row["exit_score"]),
        }
        row["train"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["train_start"], end=windows["train_end"], cost_label="2x")
        row["validation"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["validation_start"], end=windows["validation_end"], cost_label="2x")
        row["training_gate"] = _training_gate(row, prereg)
    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["validation"]["stats"]["sharpe"]), reverse=True)
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for row in finalists:
        row["validation_bootstrap"] = r453.bootstrap(row["validation"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 100)
    bootstrap_positive = [row for row in finalists if row["validation_bootstrap"]["total_p05"] > 0.0]
    for row in bootstrap_positive[: int(prereg["selection"]["neighborhood_finalists"])]:
        row["neighborhood"] = _neighborhood(row, rows)
    stable = [row for row in bootstrap_positive if row.get("neighborhood", {}).get("pass_rate", 0.0) >= prereg["selection"]["neighborhood_pass_rate_min"]]
    stable.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["validation_bootstrap"]["total_p05"]), reverse=True)
    selected = stable[0] if stable else (bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else rows[0]))
    sample = {
        "weights": selected["weights"],
        "direction": selected["direction"],
        "max_positions": int(selected["max_positions"]),
        "max_hold_days": int(selected["max_hold_days"]),
        "max_exposure_pct": 1.0,
        "entry_score": float(selected["entry_score"]),
        "exit_score": float(selected["exit_score"]),
    }
    selected["test"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["test_start"], end=windows["test_end"], cost_label="2x")
    selected["recent"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["recent_start"], end=windows["test_end"], cost_label="2x")
    selected["test_3x"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["test_start"], end=windows["test_end"], cost_label="3x")
    selected["test_5x"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["test_start"], end=windows["test_end"], cost_label="5x")
    selected["test_bootstrap"] = r453.bootstrap(selected["test"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 1)
    selected["recent_bootstrap"] = r453.bootstrap(selected["recent"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 2)
    checks = {
        "training_platform_exists": bool(eligible),
        "validation_bootstrap_positive": selected.get("validation_bootstrap", {}).get("total_p05", -1.0) > 0.0,
        "neighborhood_platform_pass": bool(selected.get("neighborhood")) and selected["neighborhood"]["pass_rate"] >= prereg["selection"]["neighborhood_pass_rate_min"],
        "test_annual_min": selected["test"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["test_annual_min"],
        "test_sharpe_min": selected["test"]["stats"]["sharpe"] >= prereg["confirmation_gates"]["test_sharpe_min"],
        "test_mdd_min": selected["test"]["stats"]["max_drawdown"] >= prereg["confirmation_gates"]["test_mdd_min"],
        "recent_annual_min": selected["recent"]["stats"]["annual_return"] >= prereg["confirmation_gates"]["recent_annual_min"],
        "stress_3x_positive": selected["test_3x"]["stats"]["annual_return"] > 0.0,
        "test_total_p05_positive": selected["test_bootstrap"]["total_p05"] > 0.0,
        "recent_total_p05_positive": selected["recent_bootstrap"]["total_p05"] > 0.0,
        "no_leverage": True,
    }
    payload = {
        "round": "R470",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__)),
        "selection_basis": "train_and_validation_only",
        "test_used_for_selection": False,
        "recent_used_for_selection": False,
        "sample_count": len(rows),
        "eligible_count": len(eligible),
        "bootstrap_positive_count": len(bootstrap_positive),
        "neighborhood_stable_count": len(stable),
        "samples": rows,
        "selected": selected,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True, "test_5x": selected["test_5x"]["stats"]},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    for row in payload["samples"]:
        row["train"].pop("curve", None)
        row["validation"].pop("curve", None)
    for key in ("train", "validation", "test", "recent", "test_3x", "test_5x"):
        payload["selected"][key].pop("curve", None)
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R470 value_quality_safety 独立 sleeve 确认",
        "",
        "本轮只检验 R452 的 `value_quality_safety`，不改变其统计证据，也不覆盖 R464 的组合结果。",
        "",
        f"- 样本数：{len(rows)}",
        f"- 训练+验证合格：{len(eligible)}",
        f"- 验证 bootstrap 正尾：{len(bootstrap_positive)}",
        f"- 邻域稳定：{len(stable)}",
        f"- 选中：`{selected['label']}`",
        f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**",
        f"- 检验年化：{selected['test']['stats']['annual_return']:.2%}",
        f"- 检验 Sharpe：{selected['test']['stats']['sharpe']:.3f}",
        f"- 检验 MDD：{selected['test']['stats']['max_drawdown']:.2%}",
        f"- 近一年年化：{selected['recent']['stats']['annual_return']:.2%}",
        f"- 检验 p05：{selected['test_bootstrap']['total_p05']:.2%}",
        f"- 近一年 p05：{selected['recent_bootstrap']['total_p05']:.2%}",
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
