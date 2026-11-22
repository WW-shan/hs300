#!/usr/bin/env python3
"""R525: quantify the membership-vintage confound without a false winner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data/factor_research/research"
PREREG = E / "r525_unified_membership_generality_audit_preregistration.json"
OUTPUT = E / "r525_unified_membership_generality_audit_2026-11-23.json"
RESULT = E / "r525_unified_membership_generality_audit_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R525 output exists; refusing overwrite: {path.name}")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R525 preregistration checksum mismatch")
    prereg = load(PREREG)
    sources = {name: load(ROOT / path) for name, path in prereg["source_rounds"].items()}
    r385 = sources["r385"]
    r506 = sources["r506"]
    r522 = sources["r522"]
    r524 = sources["r524"]
    r385_metrics = r385["current_portfolio"]["center_2x"]
    r506_metrics = r506["result"]["test"]["stats"]
    r506_recent = r506["result"]["recent"]["stats"]
    freshness = r522["summary"]["latest_full_year"]
    checks = {
        "all_sources_exist": all((ROOT / path).exists() for path in prereg["source_rounds"].values()),
        "r385_historical_membership_evidence_present": bool(
            r385.get("input_lineage", {}).get("input_audit", {}).get("membership")
        ),
        "r506_current_membership_evidence_present": any(
            "Current HS300 membership" in item for item in r506.get("known_limits", [])
        ),
        "r524_not_directly_rankable": r524["comparability"]["verdict"] == "not_directly_rankable",
        "no_execution_artifacts": all(
            item.get("decision", {}).get("orders_generated") is False
            and item.get("decision", {}).get("paper_target_generated") is False
            and item.get("decision", {}).get("production_enabled") is False
            for item in sources.values()
        ),
    }
    payload = {
        "round": "R525",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_hashes": {
            name: sha(ROOT / path) for name, path in prereg["source_rounds"].items()
        },
        "comparison": {
            "r385_historical_membership": {
                "status": r385["decision"]["status"],
                "full_annual": r385_metrics["full"]["annual"],
                "full_sharpe": r385_metrics["full"]["sharpe"],
                "full_mdd": r385_metrics["full"]["mdd"],
                "membership_sha256": r385["engine_identity"]["membership_sha256"],
                "window": ["2022-05-05", "2026-11-05"],
            },
            "r506_current_membership": {
                "status": r506["decision"]["status"],
                "test_annual": r506_metrics["annual_return"],
                "test_sharpe": r506_metrics["sharpe"],
                "test_mdd": r506_metrics["max_drawdown"],
                "recent_annual": r506_recent["annual_return"],
                "recent_sharpe": r506_recent["sharpe"],
                "recent_mdd": r506_recent["max_drawdown"],
                "window": [r506["benchmark_returns"].get("test") is not None and "2025-05-06", "2026-11-05"],
                "fresh_start_positive_count": r522["summary"]["positive_count"],
                "fresh_start_worst_p05": r522["summary"]["worst_bootstrap_total_p05"],
                "fresh_start_latest_annual": freshness["stats_2x"]["annual_return"],
                "fresh_start_latest_mdd": freshness["stats_2x"]["max_drawdown"],
            },
            "different_vintages": True,
            "causal_claim": False,
            "direct_winner": None,
            "explanation": (
                "R385/R386 passed on repaired daily membership; R506/R522 were evaluated on "
                "current membership backfilled over history. Headline returns are descriptive, "
                "not a controlled comparison."
            ),
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
        "verdict": {
            "direct_winner": None,
            "reason": "membership vintage and industry vintage differ",
            "next_action": "rerun both candidate families on one frozen PIT input bundle",
        },
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    historical = payload["comparison"]["r385_historical_membership"]
    current = payload["comparison"]["r506_current_membership"]
    lines = [
        "# R525 名单口径泛化审计",
        "",
        "## 两套证据",
        "",
        "| 版本 | 状态 | 全窗/检验年化 | Sharpe | MDD | 名单口径 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
        f"| R385/R386 | {historical['status']} | {historical['full_annual']:.2%} | {historical['full_sharpe']:.3f} | {historical['full_mdd']:.2%} | 逐日历史 HS300 |",
        f"| R506 | {current['status']} | {current['test_annual']:.2%} | {current['test_sharpe']:.3f} | {current['test_mdd']:.2%} | 当前名单回填历史 |",
        f"| R522 fresh-start | {current['status']} | {current['fresh_start_latest_annual']:.2%} | — | {current['fresh_start_latest_mdd']:.2%} | 当前名单回填历史 |",
        "",
        "## 判定",
        "",
        "- 直接赢家：**无**。",
        "- 原因：两代策略的名单 vintage、行业 vintage 和窗口都不相同。",
        "- R506 fresh-start 正收益窗口：{}；最差 p05：{:.2%}。".format(
            f"{current['fresh_start_positive_count']}/5", current["fresh_start_worst_p05"]
        ),
        "",
        "## 不能推出的结论",
        "",
        "- 不能说 R385/R386 的 13.75% 年化一定优于 R506。",
        "- 不能说 R506 的近一年表现一定更稳健。",
        "- 不能把“当前名单回填”后的亏损归因于因子失效，除非在同一份 PIT 输入上重跑。",
        "",
        "## 下一轮合法动作",
        "",
        "- 先将 R381 的逐日名单、行业历史和扩展帧封装成可提交的冻结输入 bundle。",
        "- 再用同一个 runner 重跑 R385/R386 与 R499/R506 两代候选。",
        "",
        "## 边界",
        "",
    ] + [f"- {item}" for item in prereg["known_limits"]] + [""]
    text = "\n".join(lines)
    RESULT.write_text(text, encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha(RESULT)}  {RESULT.name}\n", encoding="ascii"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
