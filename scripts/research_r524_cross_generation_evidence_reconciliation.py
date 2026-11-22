#!/usr/bin/env python3
"""R524: reconcile historical-membership and current-membership candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r524_cross_generation_evidence_reconciliation_preregistration.json"
OUTPUT = E / "r524_cross_generation_evidence_reconciliation_2026-11-23.json"
RESULT = E / "r524_cross_generation_evidence_reconciliation_2026-11-23.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    for path in (OUTPUT, RESULT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R524 output exists; refusing overwrite: {path.name}")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]:
        raise RuntimeError("R524 preregistration checksum mismatch")
    prereg = load(PREREG)
    sources = {name: load(ROOT / path) for name, path in prereg["source_rounds"].items()}
    sidecars = {
        name: sha(ROOT / path)
        == (ROOT / path).with_name(Path(path).name + ".sha256").read_text(encoding="ascii").split()[0]
        for name, path in prereg["source_rounds"].items()
    }
    r385 = sources["r385"]
    r386 = sources["r386"]
    r499 = sources["r499"]
    r506 = sources["r506"]
    r522 = sources["r522"]
    r385_membership = r385["engine_identity"]["membership_sha256"]
    r385_extension = r385["engine_identity"]["extension_frame_sha256"]
    r499_limits = " ".join(r499.get("known_limits", []))
    r506_limits = " ".join(r506.get("known_limits", []))
    r522_limits = " ".join(r522.get("known_limits", []))
    checks = {
        "all_sources_exist": all((ROOT / path).exists() for path in prereg["source_rounds"].values()),
        "all_sidecars_match": all(sidecars.values()),
        "r385_r386_candidate_status_recorded": (
            r385["decision"]["status"] == "candidate_for_paper_trading_review"
            and r386["decision"]["status"] == "candidate_for_paper_trading_review"
        ),
        "r499_r506_rejected_status_recorded": (
            r499["decision"]["status"] == "rejected"
            and r506["decision"]["status"] == "rejected"
        ),
        "no_execution_artifacts": all(
            item.get("decision", {}).get("orders_generated") is False
            and item.get("decision", {}).get("paper_target_generated") is False
            and item.get("decision", {}).get("production_enabled") is False
            for item in sources.values()
        ),
    }
    payload = {
        "round": "R524",
        "classification": prereg["classification"],
        "preregistration_sha256": sha(PREREG),
        "source_hashes": {
            name: sha(ROOT / path) for name, path in prereg["source_rounds"].items()
        },
        "comparability": {
            **prereg["comparability"],
            "r385_r386_engine": {
                "membership_sha256": r385_membership,
                "extension_frame_sha256": r385_extension,
            },
            "r499_r506_candidate": {
                "r499_rejected": r499["decision"]["status"] == "rejected",
                "r506_rejected": r506["decision"]["status"] == "rejected",
                "r522_rejected": r522["decision"]["status"] == "rejected",
                "r499_limit_mentions_current_membership": "Current HS300 membership" in r499_limits,
                "r506_limit_mentions_current_membership": "Current HS300 membership" in r506_limits,
                "r522_limit_mentions_current_membership": "Current HS300 membership" in r522_limits,
            },
            "verdict": "not_directly_rankable",
        },
        "gate": {"pass": all(checks.values()), "checks": checks},
        "next_comparison": prereg["next_comparison"],
        "decision": {**prereg["output_policy"]},
        "known_limits": prereg["known_limits"],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii"
    )
    lines = [
        "# R524 跨代策略证据统一审计",
        "",
        "- R385/R386：修复后的逐日 HS300 名单 + 申万行业生效日历史。",
        "- R499/R506：当前官方 HS300 名单回填历史。",
        "- 直接比较结论：**不可排名**，两代证据的数据血缘不同。",
        "- R385/R386 状态：candidate_for_paper_trading_review（研究候选，未上线）。",
        "- R499/R506/R522 状态：rejected（严格门槛未过）。",
        "",
        "## 门槛",
        "",
    ]
    lines += [f"- {name}: {'通过' if value else '未过'}" for name, value in checks.items()]
    lines += [
        "",
        "## 下一轮唯一合法比较",
        "",
        "- 必须使用同一份 PIT 名单、同一份行业 vintage、同一组成本口径重新跑两代候选。",
        "- 在重跑完成前，不得用 R385/R386 的历史成绩宣称优于 R506。",
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
