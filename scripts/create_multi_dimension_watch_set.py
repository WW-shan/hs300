#!/usr/bin/env python3
"""Create the diversified multi-dimension watch set from completed research."""
from __future__ import annotations

import json
from pathlib import Path

from app.factor_research.fingerprints import sha256_file
from app.factor_research.workflow import ensure_watch_factor_set

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
IC_RESEARCH = DATA / "factor_research" / "research" / "alt_hs300_rank_ic_5d_2026-11-10_v2.json"
INCREMENTAL = DATA / "factor_research" / "research" / "alt_hs300_incremental_2026-11-10.json"

MEMBERS = [
    (
        "ext_alt_hs300_idio_vol_40d_low",
        "价格风险：HS300残差低波；20/40/60/120日参数平台稳定，40日全样本IC最高。",
    ),
    (
        "ext_alt_hs300_dividend_yield",
        "价值/现金回报：股息率IC高且行业规模中性后仍稳定，和核心腿相关性较低。",
    ),
    (
        "ext_alt_hs300_vol_of_vol_20d_low",
        "波动稳定性：20日波动率的20日稳定性；参数扫描优于当前60日版本。",
    ),
    (
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "财务质量：经营现金流/总资产四季稳定性，行业规模中性后仍有正IC。",
    ),
    (
        "ext_alt_hs300_fund_holding_ratio_low",
        "资金拥挤：低基金持仓比例，近一年与全样本IC同向。",
    ),
    (
        "ext_alt_hs300_ocf_margin_4q",
        "现金盈利：TTM经营现金流/营收，补充利润质量维度。",
    ),
    (
        "ext_alt_hs300_margin_chg_1m_low",
        "交易资金：融资余额月度下降，低拥挤且和现有腿相关性低。",
    ),
    (
        "ext_alt_hs300_holder_top10_share_chg_yoy",
        "股东结构：前十大流通股东持股同比增加，增量IC为正且相关性低。",
    ),
]


def main() -> int:
    evidence_refs = [
        {
            "kind": "factor_signal_scan",
            "path": str(IC_RESEARCH.relative_to(DATA)),
            "sha256": sha256_file(IC_RESEARCH),
        },
        {
            "kind": "factor_increment_scan",
            "path": str(INCREMENTAL.relative_to(DATA)),
            "sha256": sha256_file(INCREMENTAL),
        },
    ]
    members = [
        {
            "factor_id": factor_id,
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 0.125,
            "rationale": rationale,
            "evidence_refs": evidence_refs,
        }
        for factor_id, rationale in MEMBERS
    ]
    version = ensure_watch_factor_set(
        DATA,
        "multi_dimension_v1",
        members,
        "建立跨价格风险、财务质量、资金拥挤与股东结构的多维 watch 因子集",
    )
    print(json.dumps({
        "set_id": version.set_id,
        "version": version.version,
        "member_count": len(version.members),
        "catalog_snapshot_id": version.catalog_snapshot_id,
        "content_sha256": version.content_sha256,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
