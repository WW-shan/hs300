#!/usr/bin/env python3
"""R451: read-only synthesis of the current research frontier."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r451_frontier_synthesis_preregistration.json"
OUTPUT = E / "r451_frontier_synthesis_2026-11-22.json"
RESULT = E / "r451_frontier_synthesis_2026-11-22.md"

INPUTS = {
    "r418": E / "r418_factor_admission_audit_2026-11-22.json",
    "r425": E / "r425_research_synthesis_2026-11-22.json",
    "r442": E / "r442_dual_admitted_portfolio_2026-11-22.json",
    "r443": E / "r443_dual_admitted_tail_control_2026-11-22.json",
    "r444": E / "r444_dual_admitted_risk_budget_2026-11-22.json",
    "r448": E / "r448_defensive_etf_allocation_2026-11-22.json",
    "r449": E / "r449_five_factor_composite_2026-11-22.json",
    "r450": E / "r450_r418_neutral_amendment_2026-11-22.json",
    "r433": E / "r433_parameter_audit_2026-11-22.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R451 output exists: {path.name}")
    prereg = load(PREREG)
    data = {name: load(path) for name, path in INPUTS.items()}
    weighted = data["r442"]
    best_point = max(
        weighted["grid"],
        key=lambda row: float(row["test_2x"]["stats"]["annual_return"] or 0),
    )
    selected = weighted["selected"]
    five = data["r449"]["selected"]
    etf = data["r448"]["selected"]
    frontier = {
        "factor_library_total": 235,
        "research_evidence_count": 118,
        "r391_statistical_pass_count": 11,
        "r418_admitted_count": int(data["r418"]["admitted_count"]),
        "r418_admitted": list(data["r418"]["admitted"]),
        "r450_neutral_admitted": list(data["r450"]["admitted"]),
        "parameter_family_count": 62,
        "parameter_recommended_count": 11,
        "parameter_rejected_count": 51,
        "current_best_hs300": {
            "selection": {
                "max_positions": selected["max_positions"],
                "max_hold_days": selected["max_hold_days"],
                "test_annual": selected["test_2x"]["stats"]["annual_return"],
                "test_sharpe": selected["test_2x"]["stats"]["sharpe"],
                "test_mdd": selected["test_2x"]["stats"]["max_drawdown"],
                "test_p05": selected["test_bootstrap"]["total_p05"],
                "recent_p05": selected["recent_bootstrap"]["total_p05"],
            },
            "best_point_estimate_not_selection": {
                "max_positions": best_point["max_positions"],
                "max_hold_days": best_point["max_hold_days"],
                "test_annual": best_point["test_2x"]["stats"]["annual_return"],
                "test_sharpe": best_point["test_2x"]["stats"]["sharpe"],
                "test_mdd": best_point["test_2x"]["stats"]["max_drawdown"],
                "test_p05": best_point["test_bootstrap"]["total_p05"],
                "recent_p05": best_point["recent_bootstrap"]["total_p05"],
            },
            "status": "rejected_on_negative_tail",
        },
        "five_factor": {
            "positions": five["positions"],
            "industry_cap": five["industry_cap"],
            "test_annual": five["test"]["stats"]["annual_return"],
            "test_sharpe": five["test"]["stats"]["sharpe"],
            "test_mdd": five["test"]["stats"]["max_drawdown"],
            "test_increment_vs_four_factor": data["r449"]["test_increment_annual"],
            "test_p05": five["test_bootstrap"]["total_p05"],
            "recent_p05": five["recent_bootstrap"]["total_p05"],
            "status": "rejected",
        },
        "defensive_etf": {
            "rule": etf["name"],
            "test_annual": etf["test_2x"]["stats"]["annual"],
            "test_sharpe": etf["test_2x"]["stats"]["sharpe"],
            "test_mdd": etf["test_2x"]["stats"]["mdd"],
            "recent_annual": etf["recent_2x"]["stats"]["annual"],
            "test_p05": etf["test_bootstrap"]["total_p05"],
            "recent_p05": etf["recent_bootstrap"]["total_p05"],
            "status": "rejected_on_negative_tail_and_recent",
        },
        "tail_controls": {
            "stop_loss": data["r443"]["decision"]["status"],
            "cash_index_vol_target": data["r444"]["decision"]["status"],
            "etf_configuration": data["r448"]["decision"]["status"],
        },
    }
    payload = {
        "round": "R451",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "input_sha256": {name: sha(path) for name, path in INPUTS.items()},
        "frontier": frontier,
        "decision": {
            "status": "no_final_no_leverage_strategy",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R451 当前研究前沿",
        "",
        "| 项目 | 结果 | 状态 |",
        "| --- | --- | --- |",
        "| 因子库 | 235 个，118 个有研究证据 | 索引 |",
        f"| R418 严格准入 | {frontier['r418_admitted_count']} 个 | 研究准入 |",
        f"| R450 行业+规模中性补充准入 | {len(frontier['r450_neutral_admitted'])}/5 个 | 降级证据 |",
        "| 参数族 MC | 62 族覆盖，11 推荐 / 51 拒绝 | 非全部合格 |",
        f"| 4 因子预注册选中 | 检验年化 {frontier['current_best_hs300']['selection']['test_annual']:.2%}，p05 {frontier['current_best_hs300']['selection']['test_p05']:.2%} | 拒绝 |",
        f"| 4 因子最佳点估计 | 10 仓 / 40 日，检验年化 {frontier['current_best_hs300']['best_point_estimate_not_selection']['test_annual']:.2%}，p05 {frontier['current_best_hs300']['best_point_estimate_not_selection']['test_p05']:.2%} | 非预注册选中，不可推荐 |",
        f"| 5 因子组合 | 检验年化 {five['test']['stats']['annual_return']:.2%}，Sharpe {five['test']['stats']['sharpe']:.3f}，p05 {five['test_bootstrap']['total_p05']:.2%} | 拒绝 |",
        f"| 防御 ETF | {etf['name']} 检验年化 {etf['test_2x']['stats']['annual']:.2%}，p05 {etf['test_bootstrap']['total_p05']:.2%} | 拒绝 |",
        "",
        "## 结论",
        "",
        "目前没有通过无杠杆、正尾部、近一年为正三项要求的最终策略。最接近可用的是 4 因子 HS300 组合的最佳点估计，但它不是预注册选中项，bootstrap 尾部仍为负；因此不能把点估计当作上线依据。",
        "",
        "## 下一步",
        "",
        "- 继续在已有严格准入因子上做组合层验证，先要求中性化后信号成立。",
        "- 参数优化只接受相邻平台、分段同号和 bootstrap 下界为正的参数。",
        "- ETF 与 HS300 的简单止损、现金底仓、波动率目标和行业轮动已经证伪，不重复试错。",
        "- 每一轮保留负知识并提交，不以单个漂亮窗口替代预注册门禁。",
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
