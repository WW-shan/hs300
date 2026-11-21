#!/usr/bin/env python3
"""R418: strict admission audit over R391 statistical passes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r418_factor_admission_audit_preregistration.json"
SOURCE = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence" / "r391_factor_library_statistical.json"
CATALOG = DATA / "factor_research" / "catalog" / "library_manifest_2026-11-22.json"
OUTPUT = E / "r418_factor_admission_audit_2026-11-22.json"
RESULT = E / "r418_factor_admission_audit_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R418 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R418 preregistration checksum mismatch")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))["candidates"]
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["records"]
    by_id = {row["factor_id"]: row for row in catalog}
    rules = prereg["rules"]
    rows: dict[str, dict[str, Any]] = {}
    for factor, stat in source.items():
        if stat.get("status") != "statistical_pass":
            continue
        manifest = by_id.get(factor, {})
        metrics = manifest.get("research") or {}
        yearly = metrics.get("ic_yearly_sign_consistency")
        recent = metrics.get("ic_5d_recent")
        q = stat.get("q_value")
        corr = stat.get("mean_abs_correlation")
        checks = {
            "statistical_pass": True,
            "yearly_sign_consistency": isinstance(yearly, (int, float)) and yearly >= rules["minimum_yearly_sign_consistency"],
            "recent_ic_positive": isinstance(recent, (int, float)) and recent > 0,
            "incremental_q_le": isinstance(q, (int, float)) and q <= rules["require_incremental_q_le"],
            "max_core_correlation_lt": isinstance(corr, (int, float)) and corr < rules["require_max_core_correlation_lt"],
        }
        rows[factor] = {
            "yearly_sign_consistency": yearly,
            "recent_ic": recent,
            "incremental_ic": stat.get("directional_delta_mean"),
            "q_value": q,
            "max_core_correlation": corr,
            "checks": checks,
            "admitted": all(checks.values()),
        }
    admitted = sorted(factor for factor, row in rows.items() if row["admitted"])
    rejected = {
        factor: [name for name, passed in row["checks"].items() if not passed]
        for factor, row in rows.items()
        if not row["admitted"]
    }
    payload = {
        "round": "R418",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_sha256": sha(SOURCE),
        "rules": rules,
        "rows": rows,
        "admitted": admitted,
        "admitted_count": len(admitted),
        "rejected_reasons": rejected,
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R418 因子库严格准入审计", "", "| 因子 | 年度同号 | 近一年IC | 增量IC | q | 最大相关 | 准入 |", "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for factor, row in sorted(rows.items()):
        lines.append(f"| {factor} | {row['yearly_sign_consistency']} | {row['recent_ic']} | {row['incremental_ic']} | {row['q_value']} | {row['max_core_correlation']} | {'是' if row['admitted'] else '否'} |")
    lines += ["", f"- 准入因子数：{len(admitted)}", f"- 准入名单：{', '.join(admitted) if admitted else '无'}", "", "## 拒绝原因", ""]
    lines += [f"- {factor}: {', '.join(reasons)}" for factor, reasons in sorted(rejected.items())]
    lines += ["", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
