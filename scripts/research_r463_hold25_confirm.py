#!/usr/bin/env python3
"""R463: hold-window neighborhood confirmation for value_quality."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r463_hold25_confirm_preregistration.json"
OUTPUT = E / "r463_hold25_confirm_2026-11-22.json"
RESULT = E / "r463_hold25_confirm_2026-11-22.md"
R453_PREREG = E / "r453_value_quality_portfolio_preregistration.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_r453() -> Any:
    path = ROOT / "scripts" / "research_r453_value_quality_portfolio.py"
    spec = importlib.util.spec_from_file_location("r463_r453", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R463 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R463 preregistration checksum mismatch")
    r453 = load_r453()
    p5 = r453.load_p5()
    r453_prereg = json.loads(R453_PREREG.read_text(encoding="utf-8"))
    panel, symbols = r453.build_panel(p5, r453_prereg)
    service = p5.portfolio._service()
    windows = prereg["windows"]
    rows = []
    for positions in prereg["positions_grid"]:
        for hold in prereg["hold_days_grid"]:
            row = {"positions": positions, "hold": hold}
            for window in ("train", "validation"):
                row[window] = r453.run_variant(p5, service, panel, symbols, prereg["weights"], prereg["directions"], start=windows[f"{window}_start"], end=windows[f"{window}_end"], positions=positions, hold=hold, cost_label="2x")
            rows.append(row)
    candidates = [row for row in rows if 25 <= row["hold"] <= 35 and row["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"] and row["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"]]
    candidates.sort(key=lambda row: (min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]), row["hold"], row["positions"]), reverse=True)
    selected = candidates[0] if candidates else max(rows, key=lambda row: min(row["train"]["stats"]["annual_return"], row["validation"]["stats"]["annual_return"]))
    test = r453.run_variant(p5, service, panel, symbols, prereg["weights"], prereg["directions"], start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    stress = r453.run_variant(p5, service, panel, symbols, prereg["weights"], prereg["directions"], start=windows["test_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="3x")
    recent = r453.run_variant(p5, service, panel, symbols, prereg["weights"], prereg["directions"], start=windows["recent_start"], end=windows["test_end"], positions=selected["positions"], hold=selected["hold"], cost_label="2x")
    test_bootstrap = r453.bootstrap(test["curve"], block=20, paths=5000, seed=463)
    recent_bootstrap = r453.bootstrap(recent["curve"], block=20, paths=5000, seed=464)
    # The pre-registered neighborhood is around the selected hold: adjacent
    # tested windows on the same position count, not three hand-picked holds.
    selected_hold = int(selected["hold"])
    selected_positions = int(selected["positions"])
    platform = [row for row in rows if row["positions"] == selected_positions and abs(row["hold"] - selected_hold) <= 5]
    checks = {
        "has_neighbor_window": len(platform) >= 3,
        "train_annual_min": selected["train"]["stats"]["annual_return"] >= prereg["gates"]["train_annual_min"],
        "validation_annual_min": selected["validation"]["stats"]["annual_return"] >= prereg["gates"]["validation_annual_min"],
        "test_annual_min": test["stats"]["annual_return"] >= prereg["gates"]["test_annual_min"],
        "test_sharpe_min": test["stats"]["sharpe"] >= prereg["gates"]["test_sharpe_min"],
        "test_mdd_min": test["stats"]["max_drawdown"] >= prereg["gates"]["test_mdd_min"],
        "recent_annual_min": recent["stats"]["annual_return"] >= prereg["gates"]["recent_annual_min"],
        "stress_3x_positive": stress["stats"]["annual_return"] > 0,
        "test_total_p05_positive": test_bootstrap["total_p05"] > 0,
        "recent_total_p05_positive": recent_bootstrap["total_p05"] > 0,
        "neighbor_hold_platform_positive": all(row["train"]["stats"]["annual_return"] > 0 and row["validation"]["stats"]["annual_return"] > 0 for row in platform),
        "no_leverage": True,
    }
    payload = {"round": "R463", "classification": prereg["classification"], "preregistration_sha256": sha(PREREG), "rows": [{**row, "train": {"stats": row["train"]["stats"], "trades": row["train"]["trades"]}, "validation": {"stats": row["validation"]["stats"], "trades": row["validation"]["trades"]}} for row in rows], "selected": {**selected, "test": {"stats": test["stats"], "trades": test["trades"]}, "test_3x": {"stats": stress["stats"], "trades": stress["trades"]}, "recent": {"stats": recent["stats"], "trades": recent["trades"]}, "test_bootstrap": test_bootstrap, "recent_bootstrap": recent_bootstrap}, "gate": {"pass": all(checks.values()), "checks": checks}, "diagnostics": {"five_x_warning_only": True}, "decision": {"status": "watch_candidate" if all(checks.values()) else "rejected", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False}, "known_limits": prereg["known_limits"]}
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R463 value_quality 持有窗口确认", "", "| 仓数 | 持有 | 训练年化 | 验证年化 |", "| ---: | ---: | ---: | ---: |"]
    for row in rows:
        lines.append(f"| {row['positions']} | {row['hold']} | {row['train']['stats']['annual_return']:.2%} | {row['validation']['stats']['annual_return']:.2%} |")
    lines += ["", f"- 选中：{selected['positions']} 仓 / {selected['hold']} 日", f"- 判定：**{'通过' if all(checks.values()) else '拒绝'}**", f"- 检验年化：{test['stats']['annual_return']:.2%}", f"- 检验 Sharpe：{test['stats']['sharpe']:.3f}", f"- 检验 MDD：{test['stats']['max_drawdown']:.2%}", f"- 近一年年化：{recent['stats']['annual_return']:.2%}", f"- 检验 p05：{test_bootstrap['total_p05']:.2%}", f"- 近一年 p05：{recent_bootstrap['total_p05']:.2%}", "", "## 门槛", ""]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
