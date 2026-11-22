#!/usr/bin/env python3
"""R475: standalone PEAD sleeve confirmation for forecast/express surprises."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r475_earnings_surprise_standalone_preregistration.json"
OUTPUT = E / "r475_earnings_surprise_standalone_2026-11-22.json"
RESULT = E / "r475_earnings_surprise_standalone_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def ensure_outputs_absent() -> None:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R475 output exists; refusing overwrite: {path.name}")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def generate_samples() -> list[dict[str, Any]]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for sleeve, spec in prereg["sleeves"].items():
        for positions in prereg["parameter_space"]["max_positions"]:
            for hold in prereg["parameter_space"]["max_hold_days"]:
                for entry in prereg["parameter_space"]["entry_score"]:
                    for exit_ in prereg["parameter_space"]["exit_score"]:
                        rows.append(
                            {
                                "label": f"{sleeve}|p{positions}|h{hold}|e{int(entry)}|x{int(exit_)}",
                                "sleeve": sleeve,
                                "weights": spec["weights"],
                                "directions": spec["directions"],
                                "max_positions": positions,
                                "max_hold_days": hold,
                                "entry_score": entry,
                                "exit_score": exit_,
                                "max_exposure_pct": 1.0,
                            }
                        )
    return rows


def build_panel(p5: Any) -> tuple[Any, list[str], dict[str, Any]]:
    from app.factor_research.extended_parameter_data import load_existing_ext_factors
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(ROOT / "data", current_symbols(), [])
    fields = {
        "ext_alt_hs300_forecast_surprise_60d": "forecast_surprise_60d",
        "ext_alt_hs300_express_surprise_60d": "express_surprise_60d",
    }
    panel = panel.join(load_existing_ext_factors(ROOT / "data", fields), on=["symbol", "date"], how="left").sort(["symbol", "date"])
    coverage = {}
    for factor in fields:
        nonnull = panel.filter(panel[factor].is_not_null())
        coverage[factor] = {"non_null_rows": nonnull.height, "non_null_ratio": round(nonnull.height / panel.height, 6), "first_value_date": str(nonnull["date"].min())}
    return panel, sorted(panel["symbol"].unique().to_list()), coverage


def main() -> int:
    ensure_outputs_absent()
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r468 = load_module(ROOT / "scripts" / "research_r468_operating_efficiency_sleeve.py", "r475_r468")
    r464 = r468.load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r475_r464")
    r453 = r468.load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r475_r453")
    p5 = r464.load_p5()
    panel, symbols, coverage = build_panel(p5)
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = generate_samples()
    for row in rows:
        sample = {
            "weights": row["weights"],
            "direction": next(iter(row["directions"].values())),
            "max_positions": int(row["max_positions"]),
            "max_hold_days": int(row["max_hold_days"]),
            "max_exposure_pct": 1.0,
            "entry_score": float(row["entry_score"]),
            "exit_score": float(row["exit_score"]),
        }
        row["train"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["train_start"], end=windows["train_end"], cost_label="2x")
        row["validation"] = r468.run_sample(p5, service, panel, symbols, sample, start=windows["validation_start"], end=windows["validation_end"], cost_label="2x")
        row["training_gate"] = bool(
            row["train"]["stats"]["annual_return"] >= prereg["selection"]["train_annual_min"]
            and row["validation"]["stats"]["annual_return"] >= prereg["selection"]["validation_annual_min"]
            and row["validation"]["stats"]["sharpe"] >= prereg["selection"]["validation_sharpe_min"]
            and row["validation"]["stats"]["max_drawdown"] >= prereg["selection"]["validation_mdd_min"]
        )
    eligible = [row for row in rows if row["training_gate"]]
    eligible.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["validation"]["stats"]["sharpe"]), reverse=True)
    finalists = eligible[: int(prereg["selection"]["bootstrap_finalists"])]
    for row in finalists:
        row["validation_bootstrap"] = r453.bootstrap(row["validation"]["curve"], block=int(prereg["bootstrap"]["block_days"]), paths=int(prereg["bootstrap"]["replications"]), seed=int(prereg["bootstrap"]["seed"]) + 100)
    bootstrap_positive = [row for row in finalists if row["validation_bootstrap"]["total_p05"] > 0.0]
    selected = bootstrap_positive[0] if bootstrap_positive else (eligible[0] if eligible else rows[0])
    sample = {
        "weights": selected["weights"],
        "direction": next(iter(selected["directions"].values())),
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
        "round": "R475",
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
        "samples": [{**row, "train": {k: v for k, v in row["train"].items() if k != "curve"}, "validation": {k: v for k, v in row["validation"].items() if k != "curve"}} for row in rows],
        "selected": {**selected, "test": {k: v for k, v in selected["test"].items() if k != "curve"}, "recent": {k: v for k, v in selected["recent"].items() if k != "curve"}, "test_3x": {k: v for k, v in selected["test_3x"].items() if k != "curve"}, "test_5x": {k: v for k, v in selected["test_5x"].items() if k != "curve"}},
        "gate": {"pass": all(checks.values()), "checks": checks},
        "diagnostics": {"five_x_warning_only": True, "no_leverage": True},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R475 业绩预告/快报 PEAD 独立 sleeve 确认",
        "",
        "本轮只检验项目已有的预告/快报预期差字段，不覆盖 R100 的历史证据。",
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
