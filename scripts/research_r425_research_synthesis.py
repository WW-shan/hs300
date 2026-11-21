#!/usr/bin/env python3
"""R425: synthesize existing evidence into a concise research decision record."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
E = DATA / "factor_research" / "research"
PREREG = E / "r425_research_synthesis_preregistration.json"
OUTPUT = E / "r425_research_synthesis_2026-11-22.json"
RESULT = E / "r425_research_synthesis_2026-11-22.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str) -> dict[str, Any]:
    return json.loads((E / name).read_text(encoding="utf-8"))


def stats(item: dict[str, Any], key: str = "stats") -> dict[str, Any]:
    return item.get(key, {})


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R425 output exists: {path.name}")
    r418 = load("r418_factor_admission_audit_2026-11-22.json")
    r420 = load("r420_final_candidate_tail_2026-11-22.json")
    r422 = load("r422_financial_multihorizon_neutral_2026-11-22.json")
    r423 = load("r423_financial_pb_ocf_portfolio_2026-11-22.json")
    r424 = load("r424_pb_standalone_2026-11-22.json")
    r416 = load("r416_sentiment_interaction_2026-11-22.json")
    r417 = load("r417_panic_reversal_portfolio_2026-11-22.json")
    current = r420["candidates"]["current_8f"]
    compact = r420["candidates"]["compact_4f"]
    payload = {
        "round": "R425",
        "classification": r425_classification(),
        "preregistration_sha256": sha(PREREG),
        "admitted_factors": r418["admitted"],
        "rejected_statistical_pass_factors": sorted(set(r418["rows"]) - set(r418["admitted"])),
        "financial_ic_passes": r422["pass_rows"],
        "financial_portfolio": {
            "r423_selected": r423["selected_variant"],
            "r423_status": r423["decision"]["status"],
            "r424_status": r424["decision"]["status"],
            "r424_selected_test_annual": r424["selected"]["test_2x"]["stats"]["annual_return"],
        },
        "sentiment": {
            "r416_pass": [name for name, row in r416["candidates"].items() if row["status"] == "statistical_pass"],
            "r417_selected": r417["selected_variant"],
            "r417_status": r417["decision"]["status"],
        },
        "portfolio_tail": {
            "current_8f": {
                "test_annual": current["test_2x"]["stats"]["annual_return"],
                "test_sharpe": current["test_2x"]["stats"]["sharpe"],
                "test_mdd": current["test_2x"]["stats"]["max_drawdown"],
                "test_total_p05": current["bootstrap"]["test_2x"]["total_p05"],
                "recent_total_p05": current["bootstrap"]["recent_2x"]["total_p05"],
            },
            "compact_4f": {
                "test_annual": compact["test_2x"]["stats"]["annual_return"],
                "test_sharpe": compact["test_2x"]["stats"]["sharpe"],
                "test_mdd": compact["test_2x"]["stats"]["max_drawdown"],
                "test_total_p05": compact["bootstrap"]["test_2x"]["total_p05"],
                "recent_total_p05": compact["bootstrap"]["recent_2x"]["total_p05"],
            },
        },
        "decision": {
            "status": "no_final_no_leverage_strategy_passed_tail_and_recent",
            "closest_but_not_recommended": "current_8f",
            "active_version": None,
            "orders_generated": False,
            "paper_target_generated": False,
            "production_enabled": False,
        },
        "known_limits": json.loads(PREREG.read_text(encoding="utf-8"))["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    lines = [
        "# R425 研究总账：目前没有可直接上线的无杠杆策略",
        "",
        "## 因子库",
        "",
        "- R391 统计通过：11 个",
        f"- R418 严格准入：{len(payload['admitted_factors'])} 个",
        f"- 准入名单：{', '.join(payload['admitted_factors'])}",
        f"- 统计通过但被拒绝：{', '.join(payload['rejected_statistical_pass_factors'])}",
        "",
        "## 财报与情绪",
        "",
        f"- 财报多周期合格：{len(payload['financial_ic_passes'])} 个候选-周期；PB 与 OCF/债务在 5/20/60 日均过中性 IC。",
        f"- PB 加入 8 腿组合：{r423['decision']['status']}（未赢基线训练/验证）。",
        f"- 纯 PB 独立策略：{r424['decision']['status']}（检验年化 {payload['financial_portfolio']['r424_selected_test_annual']:.2%}）。",
        f"- 情绪因子统计通过：{', '.join(payload['sentiment']['r416_pass']) or '无'}；组合替换：{r417['decision']['status']}。",
        "",
        "## 当前组合尾部",
        "",
        "| 候选 | 检验年化 | Sharpe | MDD | 检验p05 | 近一年p05 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in ("current_8f", "compact_4f"):
        row = payload["portfolio_tail"][name]
        lines.append(f"| {name} | {row['test_annual']:.2%} | {row['test_sharpe']:.3f} | {row['test_mdd']:.2%} | {row['test_total_p05']:.2%} | {row['recent_total_p05']:.2%} |")
    lines += [
        "",
        "## 最终判断",
        "",
        "- **当前 8 腿是最接近可用的候选，但尾部仍为负，不能称为最终有效策略。**",
        "- 4 腿候选的检验年化更低，尾部更差；不能因为训练/验证好看就推荐。",
        "- PB/OCF 的 IC 证据成立，但独立组合和现有组合替换均失败，只能作为后续条件/滤网研究，不作为独立策略。",
        "- 情绪恐慌反转统计通过，但组合替换失败；不进入因子集。",
        "",
        "## 下一步优先级",
        "",
        "1. 先做组合层尾部控制，不再新增因子：测试持仓数、行业暴露、现金/指数底仓与再平衡规则。",
        "2. 若组合层仍不能使 p05 转正，转向纯防御资产/ETF 轮动，不再强行从 HS300 因子中找绝对收益。",
        "3. 新的财报/情绪因子只有在 R418 同等级年度稳定性 + R420 同等级尾部审计都通过后，才允许进入组合池。",
        "",
        "## 边界",
        "",
    ]
    lines += [f"- {item}" for item in payload["known_limits"]]
    lines += [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(text)
    return 0


def r425_classification() -> str:
    return "read-only synthesis of existing evidence; no new parameter search"


if __name__ == "__main__":
    raise SystemExit(main())
