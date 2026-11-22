#!/usr/bin/env python3
"""R471: reconcile current research frontier after R464-R470."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r471_frontier_reconciliation_preregistration.json"
OUTPUT = E / "r471_frontier_reconciliation_2026-11-22.json"
RESULT = E / "r471_frontier_reconciliation_2026-11-22.md"
INPUTS = {
    "r464": E / "r464_joint_factor_library_mc_2026-11-22.json",
    "r465": E / "r465_factor_library_mc_tail_diagnosis_2026-11-22.json",
    "r466": E / "r466_sleeve_risk_budget_mc_2026-11-22.json",
    "r467": E / "r467_risk_budget_degeneracy_2026-11-22.json",
    "r468": E / "r468_operating_efficiency_sleeve_2026-11-22.json",
    "r469": E / "r469_operating_efficiency_attribution_2026-11-22.json",
    "r470": E / "r470_value_quality_safety_standalone_2026-11-22.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists() or sha(path) != sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError(f"checksum mismatch: {path.name}")


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R471 output exists; refusing overwrite: {path.name}")
    verify_sidecar(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    data = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in INPUTS.items()}
    r464 = data["r464"]
    r467 = data["r467"]
    r468 = data["r468"]
    r469 = data["r469"]
    r470 = data["r470"]
    frontier = {
        "factor_library_total": 235,
        "research_evidence_count": 118,
        "r418_strict_admitted": 5,
        "r450_neutral_admitted": 2,
        "active_version": None,
        "no_leverage_final_strategy": None,
        "current_near_candidates": [
            {
                "id": "r463_four_vq_10_25",
                "members": "four dual-admitted factors + 10% value_quality",
                "positions": 10,
                "hold_days": 25,
                "test_annual": 0.2223,
                "recent_annual": 0.0959,
                "test_p05": 0.0382,
                "recent_p05": -0.1280,
                "status": "rejected_recent_tail",
            },
            {
                "id": "r464_seven_vq_5pct",
                "members": "seven_vq + 5% value_quality",
                "positions": 10,
                "hold_days": 35,
                "test_annual": 0.0809,
                "recent_annual": 0.0190,
                "test_p05": -0.1106,
                "recent_p05": -0.1740,
                "status": "rejected_validation_and_tail",
            },
            {
                "id": "r468_inventory_growth_low",
                "members": "inventory_growth_minus_cogs_growth",
                "positions": 10,
                "hold_days": 20,
                "test_annual": 0.3781,
                "recent_annual": 0.0764,
                "test_p05": 0.0220,
                "recent_p05": -0.1975,
                "validation_p05": -0.1725,
                "status": "rejected_validation_and_concentration",
            },
            {
                "id": "r470_vqs_20_30",
                "members": "value_quality_safety",
                "positions": 20,
                "hold_days": 30,
                "test_annual": 0.1707,
                "recent_annual": 0.0557,
                "test_p05": -0.0798,
                "recent_p05": -0.2191,
                "status": "rejected_mdd_and_tail",
            },
        ],
        "negative_knowledge": {
            "r464": "joint sleeve/portfolio MC: validation bootstrap zero positives",
            "r465": "tail persists under block 10/20/40",
            "r466": "sleeve risk budget rejected; 34/160 samples degenerate",
            "r467": "after removing degeneracy: 123 eligible, zero bootstrap positives",
            "r468": "operating-efficiency standalone rejected on validation bootstrap and recent tail",
            "r469": "top 10 profitable trades removal leaves only ~2.3% of total PnL",
            "r470": "value_quality_safety standalone rejected on MDD and both bootstrap tails",
        },
        "source_metrics": {
            "r464_eligible": r464["eligible_count"],
            "r464_bootstrap_positive": r464["bootstrap_positive_count"],
            "r467_degenerate": r467["degenerate_count"],
            "r467_eligible": r467["eligible_count"],
            "r467_bootstrap_positive": r467["bootstrap_positive_count"],
            "r468_eligible": r468["eligible_count"],
            "r468_bootstrap_positive": r468["bootstrap_positive_count"],
            "r469_trade_count": r469["trade_count"],
            "r469_top10_remaining_pnl": r469["top_trade_removal"]["10"]["remaining_pnl"],
            "r469_total_pnl": r469["total_pnl_amount"],
            "r470_eligible": r470["eligible_count"],
            "r470_bootstrap_positive": r470["bootstrap_positive_count"],
        },
    }
    payload = {
        "round": "R471",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "input_sha256": {name: sha(path) for name, path in INPUTS.items()},
        "frontier": frontier,
        "decision": {**prereg["output_policy"], "status": "frontier_reconciliation_complete_no_promotion"},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R471 因子研究前沿对账",
        "",
        "本轮只对账 R464-R470 的冻结证据，不重新跑参数、不改变任何拒绝结论。",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        "| 因子库 | 235 个，118 个有研究证据 |",
        "| R418 严格准入 | 5 个 |",
        "| R450 中性补充准入 | 2/5 个 |",
        "| 当前 active_version | null |",
        "| 可用无杠杆最终策略 | 无 |",
        "",
        "## 当前最接近候选",
        "",
        "| 候选 | 检验年化 | 近一年年化 | 检验 p05 | 近一年 p05 | 状态 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in frontier["current_near_candidates"]:
        lines.append(f"| {row['id']} | {row['test_annual']:.2%} | {row['recent_annual']:.2%} | {row['test_p05']:.2%} | {row['recent_p05']:.2%} | {row['status']} |")
    lines += ["", "## 负知识", ""]
    lines += [f"- {key}: {value}" for key, value in frontier["negative_knowledge"].items()]
    lines += ["", "## 下一步", "", "- 不再重复现金缓冲、固定止损、简单 regime 防守、防御 ETF 混合和 sleeve 风险预算。", "- 继续寻找有独立经济含义的新收益源，先做单因子真实 T+1、验证 bootstrap、近一年尾部和集中度归因。", "- 新候选只有全部门槛通过才进入观察集；否则保留负知识。", "", "## 边界", ""]
    lines += [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
