#!/usr/bin/env python3
"""R413: same-window idio_vol 40d vs 20d portfolio confirmation."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "factor_research" / "research"
PREREG = EVIDENCE / "r413_idio_window_preregistration.json"
OUTPUT = EVIDENCE / "r413_idio_window_2026-11-22.json"
RESULT = EVIDENCE / "r413_idio_window_2026-11-22.md"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r413_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prereg() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R413":
        raise RuntimeError("R413 preregistration round mismatch")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R413 preregistration checksum mismatch")
    baseline = payload["baseline_members"]
    variant = payload["variant_members"]
    if len(baseline) != 8 or len(variant) != 8:
        raise RuntimeError("R413 requires exactly eight members")
    if baseline[1:] != variant[1:]:
        raise RuntimeError("R413 may change only the idio_vol member")
    if baseline[0] != "ext_alt_hs300_idio_vol_40d_low":
        raise RuntimeError("R413 baseline idio_vol member mismatch")
    if variant[0] != "ext_alt_hs300_idio_vol_20d_low":
        raise RuntimeError("R413 variant idio_vol member mismatch")
    return payload


def _inputs(members: list[str], directions: dict[str, str]) -> tuple[dict[str, float], dict[str, str]]:
    weight = 1.0 / len(members)
    return (
        {member: weight for member in members},
        {member: directions[member] for member in members},
    )


def run_variant(
    p5: Any,
    service: Any,
    prepared: Any,
    members: list[str],
    directions: dict[str, str],
    *,
    start: str,
    end: str,
    cost: str,
    label: str,
) -> dict[str, Any]:
    scoring, selected = _inputs(members, directions)
    record = p5._run_prepared(
        service,
        prepared,
        scoring,
        selected,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        cost_label=cost,
        label=label,
    )
    return {
        "total_return": record["stats"]["total_return"],
        "annual_return": record["stats"]["annual_return"],
        "sharpe": record["stats"]["sharpe"],
        "max_drawdown": record["stats"]["max_drawdown"],
        "n_trades": record["stats"]["n_trades"],
        "benchmark_return": record["benchmark_return"],
    }


def compare(
    baseline: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    base_return = baseline.get("annual_return")
    variant_return = variant.get("annual_return")
    base_mdd = baseline.get("max_drawdown")
    variant_mdd = variant.get("max_drawdown")
    return {
        "annual_delta": round(float(variant_return) - float(base_return), 6)
        if isinstance(base_return, (int, float)) and isinstance(variant_return, (int, float))
        else None,
        "sharpe_delta": round(float(variant["sharpe"]) - float(baseline["sharpe"]), 6)
        if isinstance(variant.get("sharpe"), (int, float))
        and isinstance(baseline.get("sharpe"), (int, float))
        else None,
        "mdd_delta": round(float(variant_mdd) - float(base_mdd), 6)
        if isinstance(base_mdd, (int, float)) and isinstance(variant_mdd, (int, float))
        else None,
    }


def gate(
    prereg: dict[str, Any],
    *,
    train: dict[str, Any],
    test: dict[str, Any],
    train_base: dict[str, Any],
    test_base: dict[str, Any],
    stress: dict[str, Any],
) -> dict[str, Any]:
    gates = prereg["gates"]
    checks = {
        "train_annual_min": isinstance(train.get("annual_return"), (int, float))
        and train["annual_return"] >= gates["train_annual_min"],
        "test_annual_min": isinstance(test.get("annual_return"), (int, float))
        and test["annual_return"] >= gates["test_annual_min"],
        "test_sharpe_min": isinstance(test.get("sharpe"), (int, float))
        and test["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": isinstance(test.get("max_drawdown"), (int, float))
        and test["max_drawdown"] >= gates["test_mdd_min"],
        "beat_baseline_train": compare(train_base, train)["annual_delta"] is not None
        and compare(train_base, train)["annual_delta"] > 0,
        "beat_baseline_test": compare(test_base, test)["annual_delta"] is not None
        and compare(test_base, test)["annual_delta"] > 0,
        "mdd_not_worse_test": compare(test_base, test)["mdd_delta"] is not None
        and compare(test_base, test)["mdd_delta"] >= 0,
        "stress_3x_positive": isinstance(stress.get("annual_return"), (int, float))
        and stress["annual_return"] > 0,
    }
    return {"pass": all(checks.values()), "checks": checks}


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R413 output exists: {path.name}")
    prereg = load_prereg()
    p5 = load_p5()
    portfolio = p5.portfolio
    service = portfolio._service()
    panel, _ = p5.load_candidate_panel(
        DATA,
        p5.p5_symbols() if hasattr(p5, "p5_symbols") else __import__("app.hs300.current", fromlist=["current_symbols"]).current_symbols(),
        [],
    )
    all_members = sorted(set(prereg["baseline_members"]) | set(prereg["variant_members"]))
    missing = [member for member in all_members if member not in panel.columns]
    if missing:
        raise RuntimeError(f"R413 panel missing members: {missing}")
    windows = prereg["windows"]
    prepared_train = p5._prepare_shared_window(
        service,
        sorted(panel["symbol"].unique().to_list()),
        panel,
        all_members,
        start=date.fromisoformat(windows["train_start"]),
        end=date.fromisoformat(windows["train_end"]),
    )
    prepared_test = p5._prepare_shared_window(
        service,
        sorted(panel["symbol"].unique().to_list()),
        panel,
        all_members,
        start=date.fromisoformat(windows["test_start"]),
        end=date.fromisoformat(windows["test_end"]),
    )
    directions = prereg["directions"]
    run = run_variant
    baseline_train = run(p5, service, prepared_train, prereg["baseline_members"], directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label="r413_baseline_train_2x")
    variant_train = run(p5, service, prepared_train, prereg["variant_members"], directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label="r413_variant_train_2x")
    baseline_test = run(p5, service, prepared_test, prereg["baseline_members"], directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label="r413_baseline_test_2x")
    variant_test = run(p5, service, prepared_test, prereg["variant_members"], directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label="r413_variant_test_2x")
    variant_stress = run(p5, service, prepared_test, prereg["variant_members"], directions, start=windows["test_start"], end=windows["test_end"], cost="3x", label="r413_variant_test_3x")
    result_gate = gate(prereg, train=variant_train, test=variant_test, train_base=baseline_train, test_base=baseline_test, stress=variant_stress)
    payload = {
        "round": "R413",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "windows": windows,
        "baseline_members": prereg["baseline_members"],
        "variant_members": prereg["variant_members"],
        "baseline": {"train_2x": baseline_train, "test_2x": baseline_test},
        "variant": {"train_2x": variant_train, "test_2x": variant_test, "test_3x": variant_stress},
        "deltas": {"train": compare(baseline_train, variant_train), "test": compare(baseline_test, variant_test)},
        "gate": result_gate,
        "decision": {
            "status": "watch_candidate" if result_gate["pass"] else "rejected",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R413 idio_vol 40日 vs 20日同口径确认",
        "",
        "| 版本 | 训练年化 | 训练Sharpe | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 当前40日 | {baseline_train['annual_return']:.2%} | {baseline_train['sharpe']:.3f} | {baseline_test['annual_return']:.2%} | {baseline_test['sharpe']:.3f} | {baseline_test['max_drawdown']:.2%} | - |",
        f"| MC推荐20日 | {variant_train['annual_return']:.2%} | {variant_train['sharpe']:.3f} | {variant_test['annual_return']:.2%} | {variant_test['sharpe']:.3f} | {variant_test['max_drawdown']:.2%} | {variant_stress['annual_return']:.2%} |",
        "",
        f"- 判定：**{'通过' if result_gate['pass'] else '拒绝'}**",
        f"- 训练年化增量：{payload['deltas']['train']['annual_delta']:+.2%}" if payload['deltas']['train']['annual_delta'] is not None else "- 训练年化增量：-",
        f"- 检验年化增量：{payload['deltas']['test']['annual_delta']:+.2%}" if payload['deltas']['test']['annual_delta'] is not None else "- 检验年化增量：-",
        "",
        "## 门槛",
        "",
        *[f"- {key}: {'通过' if value else '未过'}" for key, value in result_gate["checks"].items()],
        "",
        "## 边界",
        "",
        *[f"- {item}" for item in prereg["known_limits"]],
        "",
    ]
    RESULT.write_text("\n".join(lines), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(RESULT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
