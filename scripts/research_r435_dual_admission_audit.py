#!/usr/bin/env python3
"""R435: audit P6 readiness plus R418 research admission for the current set."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r435_dual_admission_audit_preregistration.json"
P6 = DATA / "factor_research" / "automation" / "latest.json"
R418 = E / "r418_factor_admission_audit_2026-11-22.json"
OUTPUT = E / "r435_dual_admission_audit_2026-11-22.json"
RESULT = E / "r435_dual_admission_audit_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R435 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    latest = json.loads(P6.read_text(encoding="utf-8"))
    p6 = json.loads((DATA / latest["run_json"]).read_text(encoding="utf-8"))
    r418 = json.loads(R418.read_text(encoding="utf-8"))
    admitted = set(r418["admitted"])
    rows = {}
    for factor_id in prereg["current_set_members"]:
        readiness = p6["admission"].get(factor_id, {})
        portfolio_ready = readiness.get("portfolio_ready") is True
        research_admitted = factor_id in admitted
        reasons = list(readiness.get("reasons", []))
        if not research_admitted:
            reasons.append("research_admission_missing")
        rows[factor_id] = {
            "portfolio_ready": portfolio_ready,
            "research_admitted": research_admitted,
            "dual_admitted": portfolio_ready and research_admitted,
            "reasons": sorted(set(str(reason) for reason in reasons)),
        }
    dual = sorted(factor for factor, row in rows.items() if row["dual_admitted"])
    payload = {
        "round": "R435",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "p6_research_id": latest["research_id"],
        "r418_sha256": sha(R418),
        "rows": rows,
        "dual_admitted": dual,
        "dual_admitted_count": len(dual),
        "decision": prereg["output_policy"],
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = ["# R435 双准入审计", "", "| 因子 | P6就绪 | R418准入 | 双准入 | 原因 |", "| --- | :---: | :---: | :---: | --- |"]
    for factor, row in rows.items():
        lines.append(f"| {factor} | {'是' if row['portfolio_ready'] else '否'} | {'是' if row['research_admitted'] else '否'} | {'是' if row['dual_admitted'] else '否'} | {', '.join(row['reasons']) or '-'} |")
    lines += ["", f"- 双准入因子数：{len(dual)}", f"- 双准入名单：{', '.join(dual) if dual else '无'}", "", "## 边界", ""] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
