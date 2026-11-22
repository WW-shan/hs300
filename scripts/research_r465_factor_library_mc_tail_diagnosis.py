#!/usr/bin/env python3
"""R465: descriptive monthly and bootstrap-block diagnosis of R464."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r465_factor_library_mc_tail_diagnosis_preregistration.json"
SOURCE = E / "r464_joint_factor_library_mc_2026-11-22.json"
OUTPUT = E / "r465_factor_library_mc_tail_diagnosis_2026-11-22.json"
RESULT = E / "r465_factor_library_mc_tail_diagnosis_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_r453() -> Any:
    return load_module(ROOT / "scripts" / "research_r453_value_quality_portfolio.py", "r465_r453")


def load_r464() -> Any:
    return load_module(ROOT / "scripts" / "research_r464_joint_factor_library_mc.py", "r465_r464")


def monthly(curve: list[dict[str, Any]]) -> dict[str, Any]:
    if not curve:
        return {"months": [], "worst_month": None, "best_month": None, "negative_months": 0, "positive_months": 0}
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in curve:
        groups.setdefault(str(item["date"])[:7], []).append(item)
    rows = []
    for month, items in sorted(groups.items()):
        start = float(items[0]["value"])
        end = float(items[-1]["value"])
        rows.append({"month": month, "return": end / start - 1.0 if start else 0.0, "start": items[0]["date"], "end": items[-1]["date"]})
    worst = min(rows, key=lambda row: row["return"])
    best = max(rows, key=lambda row: row["return"])
    return {"months": rows, "worst_month": worst, "best_month": best, "negative_months": sum(row["return"] < 0 for row in rows), "positive_months": sum(row["return"] > 0 for row in rows)}


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R465 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    selected = source["selected"]
    r453 = load_r453()
    r464 = load_r464()
    p5 = r464.load_p5()
    panel, symbols = r464.build_panel(p5)
    service = p5.portfolio._service()
    monthly_rows = {}
    boot = {}
    for window in ("validation", "test", "recent"):
        curve = r464._run_config(
            p5,
            service,
            panel,
            symbols,
            selected,
            start=prereg["windows"][f"{window}_start"],
            end=prereg["windows"]["test_end"] if window == "recent" else prereg["windows"][f"{window}_end"],
            cost_label="2x",
        )["curve"]
        monthly_rows[window] = monthly(curve)
        boot[window] = {
            str(block): r453.bootstrap(
                curve,
                block=block,
                paths=int(prereg["diagnostics"]["bootstrap_replications"]),
                seed=int(prereg["diagnostics"]["seed"]) + block,
            )
            for block in prereg["diagnostics"]["bootstrap_sensitivity_blocks"]
        }
    payload = {
        "round": "R465",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_round": "R464",
        "source_sample_label": selected["label"],
        "source_sleeve": selected["sleeve"],
        "source_weights": selected["weights"],
        "monthly": monthly_rows,
        "bootstrap_sensitivity": boot,
        "decision": {**prereg["output_policy"], "status": "factor_library_mc_tail_diagnosis_complete_no_promotion"},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R465 R464 尾部诊断", "", f"- 样本：`{selected['label']}` / {selected['sleeve']}", ""]
    lines += ["| 窗口 | 最差月 | 收益 | 负月数 | 正尾 p05 | 中位 p50 |", "| --- | --- | ---: | ---: | ---: | ---: |"]
    for window in ("validation", "test", "recent"):
        row = monthly_rows[window]
        worst = row["worst_month"]
        primary = boot[window]["20"]
        lines.append(f"| {window} | {worst['month']} | {worst['return']:.2%} | {row['negative_months']}/{len(row['months'])} | {primary['total_p05']:.2%} | {primary['total_p50']:.2%} |")
    lines += ["", "## Bootstrap 块长敏感性", "", "| 窗口 | block=10 p05 | block=20 p05 | block=40 p05 |", "| --- | ---: | ---: | ---: |"]
    for window in ("validation", "test", "recent"):
        b = boot[window]
        lines.append(f"| {window} | {b['10']['total_p05']:.2%} | {b['20']['total_p05']:.2%} | {b['40']['total_p05']:.2%} |")
    lines += ["", "## 结论", "", "这是描述性尾部诊断，不重新选参，也不改变 R464 的拒绝结论。若三个块长下 p05 均偏负，说明问题不是单一块长设置，而是收益分布本身的下尾；下一轮应做独立的风险预算/尾部控制，而不是继续在同一组样本上挑参数。", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
