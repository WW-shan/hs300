#!/usr/bin/env python3
"""R433: publish the unified parameter-family audit summary."""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from app.factor_research.parameter_audit import build_parameter_audit_summary

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r433_parameter_audit_preregistration.json"
OUTPUT = E / "r433_parameter_audit_2026-11-22.json"
RESULT = E / "r433_parameter_audit_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R433 output exists: {path.name}")
    if not PREREG.exists():
        raise RuntimeError("R433 preregistration missing")
    summary = build_parameter_audit_summary(data_dir=DATA)
    payload = {
        "round": "R433",
        "classification": "read-only unified parameter-family audit; no promotion",
        "preregistration_sha256": sha(PREREG),
        "generated_at": date.today().isoformat(),
        "summary": summary,
        "decision": {
            "status": "parameter_audit_complete_no_promotion",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R433 参数族统一审计",
        "",
        f"- 注册参数族：{summary['registry_family_count']}",
        f"- MC 覆盖：{summary['covered_family_count']}",
        f"- 缺失族：{summary['missing_family_ids']}",
        f"- 推荐参数：{summary['recommendation_count']}",
        f"- 拒绝族：{summary['rejected_count']}",
        f"- 孤儿推荐（有推荐但不在当前 8 腿）：{len(summary['orphan_recommendations'])}",
        "",
        "## 当前 8 腿中可调参数族",
        "",
        "| 参数族 | 当前参数 | 推荐参数 | 当前集合成员 | 结论 |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for row in summary["current_set_tunable_families"]:
        lines.append(f"| {row['family_id']} | {row['current_parameter']} | {row['recommended_parameter']} | {row['current_set_member']} | {row['recommendation_status']} |")
    lines += ["", "## 孤儿推荐", ""]
    if summary["orphan_recommendations"]:
        for row in summary["orphan_recommendations"]:
            lines.append(f"- {row['family_id']} -> {row['recommended_parameter']}（成员：{', '.join(row['member_factor_ids'])}）")
    else:
        lines.append("- 无")
    lines += ["", "## 边界", "", "- 这是参数族审计，不是新的回测。", "- 推荐参数是族内稳健候选，不等于组合最优。", "- 不生成订单、纸面目标或生产配置。", ""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
