#!/usr/bin/env python3
"""R510: consolidate the post-R508 factor-library frontier."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
CATALOG = ROOT / "data" / "factor_research" / "catalog" / "library_manifest_2026-11-22.json"
R391 = (
    ROOT
    / "data"
    / "strategies"
    / "ai"
    / ".iterations"
    / "ai_hs300_multi_factor_rank"
    / "evidence"
    / "r391_factor_library_statistical.json"
)
R418 = E / "r418_factor_admission_audit_2026-11-22.json"
R499 = E / "r499_weak_state_value_quality_tilt_2026-11-22.json"
R503 = E / "r503_r499_rolling_year_regime_confirmation_2026-11-22.json"
R506 = E / "r506_r499_cap1_confirmation_2026-11-22.json"
R508 = E / "r508_r499_adjunct_screening_2026-11-22.json"
R509 = E / "r509_amihud_direction_correction_2026-11-22.json"
OUTPUT = E / "r510_factor_library_frontier_consolidation_2026-11-22.json"
RESULT = E / "r510_factor_library_frontier_consolidation_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R510 output exists: {path.name}")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    r391 = json.loads(R391.read_text(encoding="utf-8"))
    r418 = json.loads(R418.read_text(encoding="utf-8"))
    r499 = json.loads(R499.read_text(encoding="utf-8"))
    r503 = json.loads(R503.read_text(encoding="utf-8"))
    r506 = json.loads(R506.read_text(encoding="utf-8"))
    r508 = json.loads(R508.read_text(encoding="utf-8"))
    r509 = json.loads(R509.read_text(encoding="utf-8"))
    records = {row["factor_id"]: row for row in catalog["records"]}
    statistical = sorted(
        factor
        for factor, row in r391["candidates"].items()
        if row.get("status") == "statistical_pass"
    )
    core = set(r418["admitted"]) & set(r499["selected"]["weights"])
    remaining = sorted(set(statistical) - core - {"value_quality"})
    missing_incremental = sorted(set(r418["admitted"]) - set(r499["selected"]["weights"]))
    frontier = {
        "total_catalog": catalog["counts"]["total"],
        "with_research_evidence": catalog["counts"]["with_research_evidence"],
        "statistical_pass": len(statistical),
        "strict_admitted": len(r418["admitted"]),
        "r499_core_members": sorted(r499["selected"]["weights"]),
        "statistical_pass_not_in_r499_core": remaining,
        "strict_admitted_not_in_r499_core": missing_incremental,
        "r499_recent_p05": r499["selected"]["recent_bootstrap"]["total_p05"],
        "r503_rolling_positive": f"{r503['rolling_summary']['positive']}/{r503['rolling_summary']['count']}",
        "r503_calendar_positive": f"{sum(row['total_return'] > 0 for row in r503['calendar'])}/{len(r503['calendar'])}",
        "r506_validation_p05": r506["result"]["validation_bootstrap"]["total_p05"],
        "r506_test_p05": r506["result"]["test_bootstrap"]["total_p05"],
        "r506_recent_p05": r506["result"]["recent_bootstrap"]["total_p05"],
        "r508_eligible": r508["eligible_count"],
        "r509_eligible": r509["eligible_count"],
        "active_version": None,
    }
    payload = {
        "round": "R510",
        "classification": "post-R508/R509 factor-library frontier consolidation; no reselection; no promotion",
        "source_hashes": {
            "catalog": sha(CATALOG),
            "r391": sha(R391),
            "r418": sha(R418),
            "r499": sha(R499),
            "r503": sha(R503),
            "r506": sha(R506),
            "r508": sha(R508),
            "r509": sha(R509),
        },
        "frontier": frontier,
        "statistical_pass_records": {
            factor: {
                "data_class": records[factor]["data_class"],
                "direction": records[factor]["direction"],
                "library_state": records[factor]["library_state"],
                "in_r499_core": factor in r499["selected"]["weights"],
                "in_r508_screen": factor in r508["candidates"]
                if isinstance(r508.get("candidates"), list)
                else False,
            }
            for factor in statistical
        },
        "decision": {
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
            "status": "factor_library_frontier_consolidated",
        },
        "known_limits": [
            "This is a consolidation of existing evidence, not a new backtest.",
            "Current HS300 membership and industry classification retain survivorship bias.",
            "Test/recent windows have been observed before.",
            "No leverage and no production activation.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = (
        [
            "# R510 因子库前沿整合",
            "",
            "## 当前规模",
            "",
            f"- 因子总数：{frontier['total_catalog']}",
            f"- 有研究证据：{frontier['with_research_evidence']}",
            f"- 统计通过：{frontier['statistical_pass']}",
            f"- 严格准入：{frontier['strict_admitted']}",
            f"- R499 核心成员：{', '.join(frontier['r499_core_members'])}",
            "",
            "## 组合层结论",
            "",
            f"- R503 滚动一年正收益：{frontier['r503_rolling_positive']}",
            f"- R503 自然年正收益：{frontier['r503_calendar_positive']}",
            f"- R506 验证 p05：{frontier['r506_validation_p05']:.2%}",
            f"- R506 检验 p05：{frontier['r506_test_p05']:.2%}",
            f"- R506 近一年 p05：{frontier['r506_recent_p05']:.2%}",
            f"- R508 可用增量：{frontier['r508_eligible']}",
            f"- R509 Amihud 方向修正后可用增量：{frontier['r509_eligible']}",
            "",
            "## 未进入 R499 核心的统计通过因子",
            "",
        ]
        + [f"- `{factor}`" for factor in remaining]
        + [
            "",
            "## 结论",
            "",
            "当前因子库的统计通过因子没有在 R506 上形成训练+验证增量；继续在同一批因子上调权重或做邻域搜索，收益递减且增加过拟合风险。下一步应优先新增原始收益源或改进风控建模，而不是继续从现有 235 个因子中反复挑选。",
            "",
            "## 边界",
            "",
        ]
        + [f"- {item}" for item in payload["known_limits"]]
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
