#!/usr/bin/env python3
"""R459: read-only update of the factor-library frontier after R452-R458."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r459_factor_library_frontier_update_preregistration.json"
OUTPUT = E / "r459_factor_library_frontier_update_2026-11-22.json"
RESULT = E / "r459_factor_library_frontier_update_2026-11-22.md"
INPUTS = {
    "r451": E / "r451_frontier_synthesis_2026-11-22.json",
    "r452": E / "r452_financial_quality_composite_2026-11-22.json",
    "r453": E / "r453_value_quality_portfolio_2026-11-22.json",
    "r454": E / "r454_value_quality_defensive_mix_2026-11-22.json",
    "r455": E / "r455_value_quality_ablation_2026-11-22.json",
    "r456": E / "r456_value_quality_compact_portfolio_2026-11-22.json",
    "r457": E / "r457_value_quality_weight_stability_2026-11-22.json",
    "r458": E / "r458_emotion_conditioned_factor_return_2026-11-22.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R459 output exists: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R459 preregistration checksum mismatch")
    data = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in INPUTS.items()}
    r452 = data["r452"]
    r453 = data["r453"]
    r454 = data["r454"]
    r455 = data["r455"]
    r456 = data["r456"]
    r457 = data["r457"]
    r458 = data["r458"]
    frontier = {
        "factor_library_total": 235,
        "research_evidence_count": 118,
        "r418_admitted_count": 5,
        "r450_neutral_admitted": ["ext_alt_hs300_cash_flow_stability_4q_low", "ext_alt_hs300_vol_of_vol_60d_low"],
        "parameter_families": {"covered": 62, "recommended": 11, "rejected": 51},
        "new_research": {
            "value_quality": {
                "admitted": r452["admitted_candidates"],
                "best_neutral_ic_60d": r452["candidates"]["value_quality"]["horizons"]["60"]["neutral_ic"]["full"]["directional_ic_mean"],
                "best_increment_60d": r452["candidates"]["value_quality"]["horizons"]["60"]["incremental"]["directional_delta"],
                "weight_center": r457["center"]["weights"],
                "weight_bootstrap_ci_low": r457["bootstrap"]["ci_low"],
                "portfolio_status": r453["decision"]["status"],
                "portfolio_test_annual": r453["selected"]["test"]["stats"]["annual_return"],
                "portfolio_test_p05": r453["selected"]["test_bootstrap"]["total_p05"],
                "compact_status": r456["decision"]["status"],
                "compact_recent_p05": r456["selected"]["recent_bootstrap"]["total_p05"],
                "defensive_mix_status": r454["decision"]["status"],
                "defensive_mix_test_p05": r454["test_bootstrap"]["total_p05"],
            },
            "emotion_condition": {
                "status": r458["decision"]["status"],
                "passing_cells": [
                    {"emotion": emotion, "sleeve": sleeve, "horizon": horizon}
                    for emotion, by_sleeve in r458["states"].items()
                    for sleeve, by_horizon in by_sleeve.items()
                    for horizon, row in by_horizon.items()
                    if row["gate"]["pass"]
                ],
            },
            "ablation": {
                "best_60d_family": max(r455["rows"], key=lambda name: r455["rows"][name]["horizons"]["60"]["full"]["directional_ic_mean"]),
                "debt_only_gate": r455["rows"]["debt_only"]["horizons"]["60"]["gate"]["pass"],
                "pb_ocf_gate": r455["rows"]["pb_ocf"]["horizons"]["60"]["gate"]["pass"],
            },
        },
    }
    payload = {
        "round": "R459",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "input_sha256": {name: sha(path) for name, path in INPUTS.items()},
        "frontier": frontier,
        "decision": {**prereg["output_policy"], "status": "frontier_update_complete_no_promotion"},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R459 因子库前沿更新",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        "| 因子库 | 235 个，118 个有研究证据 |",
        "| R418 严格准入 | 5 个 |",
        "| R450 行业+规模中性补充准入 | 2/5 个 |",
        "| 参数族 MC | 62 族覆盖，11 推荐 / 51 拒绝 |",
        f"| 新财报复合 | {', '.join(r452['admitted_candidates'])} |",
        f"| value_quality 60日中性IC | {frontier['new_research']['value_quality']['best_neutral_ic_60d']:.4f} |",
        f"| value_quality 权重 bootstrap 下界 | {frontier['new_research']['value_quality']['weight_bootstrap_ci_low']:.4f} |",
        f"| value_quality 组合 | 检验年化 {r453['selected']['test']['stats']['annual_return']:.2%}，p05 {r453['selected']['test_bootstrap']['total_p05']:.2%}，拒绝 |",
        f"| 紧凑 PB+OCF 组合 | 近一年 p05 {r456['selected']['recent_bootstrap']['total_p05']:.2%}，拒绝 |",
        f"| 防御资产混合 | 检验 p05 {r454['test_bootstrap']['total_p05']:.2%}，拒绝 |",
        f"| 情绪条件 | 通过单元：{frontier['new_research']['emotion_condition']['passing_cells']} |",
        "",
        "## 结论",
        "",
        "本轮新增了可复现的财报价值/质量信号，但没有改变“尚无可用无杠杆最终策略”的结论。`value_quality` 的统计信号和权重稳定性成立，组合验证仍被近一年收益与 bootstrap 尾部否决；情绪条件只有一个 20 日单元通过，不能当成完整择时开关。",
        "",
        "## 下一步",
        "",
        "- 用 `value_quality` 做条件层，而不是继续在单一股票组合里硬拼权重。",
        "- 继续对情绪条件做 T-1 时点、分年稳定性和交易成本审计。",
        "- 把防御资产混合与股票复合因子分开验证，不把研究级混合当成可执行组合。",
        "- 每一轮都保留负知识、更新总账并提交推送。",
        "",
        "## 边界",
        "",
    ] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
