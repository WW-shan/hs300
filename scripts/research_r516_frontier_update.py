#!/usr/bin/env python3
"""R516: consolidate R511-R515 into the frontier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r516_frontier_update_preregistration.json"
OUTPUT = E / "r516_frontier_update_2026-11-22.json"
RESULT = E / "r516_frontier_update_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R516 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    r510 = json.loads((E / prereg["sources"]["r510"]).read_text(encoding="utf-8"))
    r512 = json.loads((E / prereg["sources"]["r512"]).read_text(encoding="utf-8"))
    r513 = json.loads((E / prereg["sources"]["r513"]).read_text(encoding="utf-8"))
    r514 = json.loads((E / prereg["sources"]["r514"]).read_text(encoding="utf-8"))
    r515 = json.loads((E / prereg["sources"]["r515"]).read_text(encoding="utf-8"))
    growth_passes = [
        (factor, horizon)
        for factor, by_horizon in r512["composites"].items()
        for horizon, row in by_horizon.items()
        if row.get("gate", {}).get("pass")
    ]
    industry_passes = [
        (conditioner, sleeve, horizon)
        for conditioner, by_sleeve in r513["conditioners"].items()
        for sleeve, by_horizon in by_sleeve.items()
        for horizon, row in by_horizon.items()
        if row.get("gate", {}).get("pass")
    ]
    payload = {
        "round": "R516",
        "classification": prereg["classification"],
        "source_hashes": {name: sha(E / rel) for name, rel in prereg["sources"].items()},
        "frontier": {
            "factor_total": r510["frontier"]["total_catalog"],
            "statistical_pass": r510["frontier"]["statistical_pass"],
            "strict_admitted": r510["frontier"]["strict_admitted"],
            "r508_eligible": r510["frontier"]["r508_eligible"],
            "r509_eligible": r510["frontier"]["r509_eligible"],
            "growth_passes": growth_passes,
            "industry_condition_passes": industry_passes,
            "r514_selected": r514["selected"]["name"],
            "r514_decision": r514["decision"]["status"],
            "r515_selected": r515["selected"]["name"],
            "r515_decision": r515["decision"]["status"],
            "active_version": None,
        },
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = (
        [
            "# R516 因子库前沿更新",
            "",
            f"- 因子总数：{payload['frontier']['factor_total']}",
            f"- 统计通过：{payload['frontier']['statistical_pass']}",
            f"- 严格准入：{payload['frontier']['strict_admitted']}",
            f"- R508 可用增量：{payload['frontier']['r508_eligible']}",
            f"- R509 可用增量：{payload['frontier']['r509_eligible']}",
            f"- 成长因子通过项：{growth_passes}",
            f"- 行业条件通过项：{industry_passes}",
            f"- R514 风险预算选择：`{payload['frontier']['r514_selected']}` / {payload['frontier']['r514_decision']}",
            f"- R515 修正选择：`{payload['frontier']['r515_selected']}` / {payload['frontier']['r515_decision']}",
            "",
            "## 结论",
            "",
            "成长加速度、质量确认成长、行业动量条件化和动态风险预算都没有通过严格门槛。当前没有可晋级的新收益源或风控层。",
            "",
            "## 边界",
            "",
        ]
        + [f"- {item}" for item in prereg["known_limits"]]
        + [""]
    )
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
