#!/usr/bin/env python3
"""R415: limited defensive leg swaps on the current 8-leg watch set."""
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
E = DATA / "factor_research" / "research"
PREREG = E / "r415_defensive_leg_swap_preregistration.json"
OUTPUT = E / "r415_defensive_leg_swap_2026-11-22.json"
RESULT = E / "r415_defensive_leg_swap_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_p5() -> Any:
    script_dir = ROOT / "scripts"
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    path = script_dir / "research_factor_increment_p5.py"
    spec = importlib.util.spec_from_file_location("r415_p5", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_prereg() -> dict[str, Any]:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    if payload.get("round") != "R415":
        raise RuntimeError("R415 round mismatch")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R415 preregistration checksum mismatch")
    return payload


def variants(prereg: dict[str, Any]) -> dict[str, list[str]]:
    base = list(prereg["baseline_members"])
    return {
        "baseline": base,
        "drop_dividend": [item for item in base if item != "ext_alt_hs300_dividend_yield"],
        "drop_holder": [item for item in base if item != "ext_alt_hs300_holder_top10_share_chg_yoy"],
        "drop_dividend_and_holder": [
            item for item in base
            if item not in {
                "ext_alt_hs300_dividend_yield",
                "ext_alt_hs300_holder_top10_share_chg_yoy",
            }
        ],
    }


def run(p5: Any, service: Any, prepared: Any, members: list[str], directions: dict[str, str], *, start: str, end: str, cost: str, label: str) -> dict[str, Any]:
    scoring, selected = p5._equal_inputs(members, directions)
    record = p5._run_prepared(
        service, prepared, scoring, selected,
        start=date.fromisoformat(start), end=date.fromisoformat(end),
        cost_label=cost, label=label,
    )
    stats = record["stats"]
    return {
        "total_return": stats["total_return"],
        "annual_return": stats["annual_return"],
        "sharpe": stats["sharpe"],
        "max_drawdown": stats["max_drawdown"],
        "n_trades": stats["n_trades"],
    }


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R415 output exists: {path.name}")
    prereg = load_prereg()
    p5 = load_p5()
    from app.hs300.current import current_symbols
    panel, _ = p5.load_candidate_panel(DATA, current_symbols(), [])
    missing = [member for member in prereg["baseline_members"] if member not in panel.columns]
    if missing:
        raise RuntimeError(f"R415 panel missing baseline members: {missing}")
    service = p5.portfolio._service()
    symbols = sorted(panel["symbol"].unique().to_list())
    directions = prereg["directions"]
    windows = prereg["windows"]
    prepared_train = p5._prepare_shared_window(service, symbols, panel, prereg["baseline_members"], start=date.fromisoformat(windows["train_start"]), end=date.fromisoformat(windows["train_end"]))
    prepared_validation = p5._prepare_shared_window(service, symbols, panel, prereg["baseline_members"], start=date.fromisoformat(windows["validation_start"]), end=date.fromisoformat(windows["validation_end"]))
    prepared_test = p5._prepare_shared_window(service, symbols, panel, prereg["baseline_members"], start=date.fromisoformat(windows["test_start"]), end=date.fromisoformat(windows["test_end"]))
    rows: dict[str, dict[str, Any]] = {}
    for name, members in variants(prereg).items():
        rows[name] = {
            "members": members,
            "train_2x": run(p5, service, prepared_train, members, directions, start=windows["train_start"], end=windows["train_end"], cost="2x", label=f"r415_{name}_train"),
            "validation_2x": run(p5, service, prepared_validation, members, directions, start=windows["validation_start"], end=windows["validation_end"], cost="2x", label=f"r415_{name}_validation"),
        }
    base = rows["baseline"]
    eligible = [
        (name, row)
        for name, row in rows.items()
        if name != "baseline"
        and row["train_2x"]["annual_return"] > base["train_2x"]["annual_return"]
        and row["validation_2x"]["annual_return"] > base["validation_2x"]["annual_return"]
    ]
    eligible.sort(key=lambda item: (min(item[1]["train_2x"]["annual_return"], item[1]["validation_2x"]["annual_return"]), item[0]), reverse=True)
    selected_name = eligible[0][0] if eligible else "baseline"
    selected = rows[selected_name]
    selected["test_2x"] = run(p5, service, prepared_test, selected["members"], directions, start=windows["test_start"], end=windows["test_end"], cost="2x", label=f"r415_{selected_name}_test")
    selected["test_3x"] = run(p5, service, prepared_test, selected["members"], directions, start=windows["test_start"], end=windows["test_end"], cost="3x", label=f"r415_{selected_name}_test_3x")
    gates = prereg["gates"]
    checks = {
        "selected_not_baseline": selected_name != "baseline",
        "train_annual_min": selected["train_2x"]["annual_return"] >= gates["train_annual_min"],
        "validation_annual_min": selected["validation_2x"]["annual_return"] >= gates["validation_annual_min"],
        "test_annual_min": selected["test_2x"]["annual_return"] >= gates["test_annual_min"],
        "test_sharpe_min": selected["test_2x"]["sharpe"] >= gates["test_sharpe_min"],
        "test_mdd_min": selected["test_2x"]["max_drawdown"] >= gates["test_mdd_min"],
        "beat_baseline_train": selected["train_2x"]["annual_return"] > base["train_2x"]["annual_return"],
        "beat_baseline_validation": selected["validation_2x"]["annual_return"] > base["validation_2x"]["annual_return"],
        "stress_3x_positive": selected["test_3x"]["annual_return"] > 0,
    }
    payload = {
        "round": "R415",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "selected_variant": selected_name,
        "variants": rows,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R415 防御腿替换确认",
        "",
        "| 变体 | 腿数 | 训练年化 | 验证年化 | 检验年化 | 检验Sharpe | 检验MDD | 3×检验年化 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in rows.items():
        test = row.get("test_2x")
        if test is None:
            lines.append(f"| {name} | {len(row['members'])} | {row['train_2x']['annual_return']:.2%} | {row['validation_2x']['annual_return']:.2%} | - | - | - | - |")
        else:
            lines.append(f"| {name} | {len(row['members'])} | {row['train_2x']['annual_return']:.2%} | {row['validation_2x']['annual_return']:.2%} | {test['annual_return']:.2%} | {test['sharpe']:.3f} | {test['max_drawdown']:.2%} | {row['test_3x']['annual_return']:.2%} |")
    lines += ["", f"- 选中：`{selected_name}`", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
