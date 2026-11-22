#!/usr/bin/env python3
"""R523: post-R522 factor-library frontier audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r523_frontier_audit_preregistration.json"
OUTPUT = E / "r523_frontier_audit_2026-11-23.json"
RESULT = E / "r523_frontier_audit_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str) -> dict:
    return json.loads((E / name).read_text(encoding="utf-8"))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R523 output exists; refusing overwrite: {path.name}")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R523 preregistration checksum mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    sources = {Path(name).stem: load(name) for name in prereg["sources"]}
    r510 = sources["r510_factor_library_frontier_consolidation_2026-11-22"]
    r516 = sources["r516_frontier_update_2026-11-22"]
    r520 = sources["r520_industry_index_feasibility_2026-11-22"]
    r521 = sources["r521_industry_index_momentum_2026-11-23"]
    r522 = sources["r522_r506_fresh_start_year_audit_2026-11-23"]
    checks = {
        "active_version_is_null": all(
            item.get("decision", {}).get("active_version") is None for item in sources.values()
        ),
        "no_orders_or_paper_target": all(
            item.get("decision", {}).get("orders_generated") is False
            and item.get("decision", {}).get("paper_target_generated") is False
            for item in sources.values()
        ),
        "all_sources_checksummed": all((E / name).with_name(name + ".sha256").exists() for name in prereg["sources"]),
    }
    frontier = {
        "factor_total": r510["frontier"]["total_catalog"],
        "statistical_pass": r510["frontier"]["statistical_pass"],
        "strict_admitted": r510["frontier"]["strict_admitted"],
        "industry_index_long_history": r520["gate"]["long_history_count"],
        "industry_momentum_passed": r521["gate"]["pass"],
        "r506_fresh_start_positive": f"{r522['summary']['positive_count']}/{r522['summary']['window_count']}",
        "r506_fresh_start_worst_p05": r522["summary"]["worst_bootstrap_total_p05"],
        "r506_frontier_decision": r516["decision"]["status"],
    }
    payload = {
        "round": "R523",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_hashes": {name: sha(E / name) for name in prereg["sources"]},
        "frontier": frontier,
        "gate": {"pass": all(checks.values()), "checks": checks},
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R523 因子库前沿复核（R522 后）",
        "",
        f"- 因子总数：{frontier['factor_total']}",
        f"- 统计通过：{frontier['statistical_pass']}",
        f"- 严格准入：{frontier['strict_admitted']}",
        f"- 行业指数长历史：{frontier['industry_index_long_history']}/28 申万行业通过",
        f"- 行业动量：{'通过' if frontier['industry_momentum_passed'] else '拒绝'}",
        f"- R506 fresh-start 正收益：{frontier['r506_fresh_start_positive']}",
        f"- R506 fresh-start 最差 p05：{frontier['r506_fresh_start_worst_p05']:.2%}",
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
