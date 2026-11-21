#!/usr/bin/env python3
"""R398 follow-up: yearly robustness for frozen candidates."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
SOURCE = E / "r398_strict_mc_2026-11-22.json"
PREREG = E / "r398_rolling_preregistration.json"
OUTPUT = E / "r398_rolling_2026-11-22.json"
RESULT = E / "r398_rolling_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def portfolio_module():
    path = ROOT / "scripts" / "research_factor_set_portfolio.py"
    spec = importlib.util.spec_from_file_location("r398_rolling_portfolio", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def windows() -> list[tuple[date, date]]:
    starts = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5), date(2025, 5, 6))
    return [
        (start, min(date(start.year + 1, start.month, start.day) - timedelta(days=1), date(2026, 11, 5)))
        for start in starts
    ]


def evaluate(module: Any, service: Any, candidate: dict[str, Any], start: date, end: date, cost: str) -> dict[str, Any]:
    config = module._backtest_config(
        candidate["weights"], {factor: "high" for factor in candidate["members"]},
        start=start, end=end, max_positions=int(candidate["max_positions"]),
        cost=module._cost_parameters(cost), max_hold_days=int(candidate["max_hold_days"]),
    )
    result = service.run(config)
    if result.error:
        raise RuntimeError(result.error)
    stats = result.stats
    return {
        "annual": stats.get("annual_return"), "sharpe": stats.get("sharpe"),
        "mdd": stats.get("max_drawdown"), "total_return": stats.get("total_return"),
        "benchmark_return": module._curve_return(result.benchmark_curve), "trades": len(result.trades),
    }


def main() -> int:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    candidates = [row for row in source["validation_stable_candidates"] if row.get("test_gate")][:3]
    if not PREREG.exists():
        prereg = {
            "round": "R398R", "classification": "frozen candidate yearly audit; no promotion",
            "source_sha256": sha(SOURCE), "candidates": "top 3 benchmark-relative test-pass candidates frozen from R398",
            "windows": [[str(start), str(end)] for start, end in windows()],
            "cost_protocol": {"main": "2x", "stress": "3x", "five_x_hard_rejection": False},
            "output_policy": {"status": "rolling_audit_complete_no_promotion", "active_version": None},
        }
        raw = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        PREREG.write_bytes(raw)
        PREREG.with_name(PREREG.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {PREREG.name}\n", encoding="ascii")
    if PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0] != sha(PREREG):
        raise RuntimeError("R398 rolling preregistration changed")
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R398 rolling output exists: {path.name}")
    module = portfolio_module()
    service = module._service()
    reports = []
    for index, candidate in enumerate(candidates):
        rows = []
        for start, end in windows():
            row = {"start": str(start), "end": str(end)}
            row["2x"] = evaluate(module, service, candidate, start, end, "2x")
            row["3x"] = evaluate(module, service, candidate, start, end, "3x")
            row["2x_relative"] = row["2x"]["total_return"] - row["2x"]["benchmark_return"]
            rows.append(row)
            print(json.dumps({"candidate": index, "window": row["start"], "2x": row["2x"], "relative": row["2x_relative"]}), flush=True)
        reports.append({
            "candidate": candidate, "windows": rows,
            "positive_2x": sum(row["2x"]["total_return"] > 0 for row in rows),
            "positive_relative_2x": sum(row["2x_relative"] > 0 for row in rows),
            "window_count": len(rows),
        })
    payload = {
        "round": "R398R", "classification": "frozen candidate yearly audit; no promotion",
        "runner_sha256": sha(Path(__file__)), "preregistration_sha256": sha(PREREG), "source_sha256": sha(SOURCE),
        "reports": reports,
        "decision": {"status": "rolling_audit_complete_no_promotion", "active_version": None, "orders_generated": False, "paper_target_generated": False, "production_enabled": False},
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R398 冻结候选逐年审计", "",
        "| 候选 | 2022–23 | 2023–24 | 2024–25 | 2025–26 | 2×正收益 | 2×正超额 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        cells = [f"{row['2x']['total_return']:.2%} / {row['2x_relative']:+.2%}" for row in report["windows"]]
        lines.append(f"| {report['candidate']['id']} | {' | '.join(cells)} | {report['positive_2x']}/{report['window_count']} | {report['positive_relative_2x']}/{report['window_count']} |")
    lines += ["", "## 边界", "", "- 这是冻结候选的年度审计，不再用年度结果重新挑参。", "- 2022–2023 窗口仍处于训练数据范围，不是独立样本。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{hashlib.sha256(RESULT.read_bytes()).hexdigest()}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
