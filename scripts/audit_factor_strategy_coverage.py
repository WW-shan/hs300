#!/usr/bin/env python3
"""Audit factor and strategy coverage across tracked research strategies."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.factors.registry import all_factors
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank"
    / "evidence" / "r107_factor_strategy_coverage.json"
)
LEDGER = DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "LEDGER.md"


def main() -> int:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[
            Path(__file__).resolve().parents[1] / "backend" / "app" / "strategy" / "research",
            DATA / "strategies" / "custom",
            DATA / "strategies" / "ai",
            DATA / "strategies" / "composite",
        ],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    strategies = engine.list_strategies(include_research=True)
    direct: Counter[str] = Counter()
    strategy_ids: list[str] = []
    for meta in strategies:
        sid = str(meta["id"])
        strategy_ids.append(sid)
        strategy = engine.get(sid)
        for factor_id, weight in (strategy.meta.get("scoring") or {}).items():
            if weight:
                direct[str(factor_id)] += 1

    composite_members: Counter[str] = Counter()
    for factor in all_factors(asset_type="stock"):
        if factor.components:
            for member_id, _weight in factor.components:
                composite_members[str(member_id)] += 1

    custom_status = {}
    for path in sorted((DATA / "user_data" / "custom_factors").glob("*.json")):
        try:
            definition = json.loads(path.read_text(encoding="utf-8"))
            custom_status[str(definition.get("id"))] = str(definition.get("status", "draft"))
        except (OSError, json.JSONDecodeError):
            continue

    ledger_text = LEDGER.read_text(encoding="utf-8") if LEDGER.exists() else ""
    factors = all_factors(asset_type="stock")
    items = []
    for factor in factors:
        fid = factor.id
        retired = custom_status.get(fid) == "retired"
        mentioned = fid in ledger_text
        items.append({
            "id": fid,
            "label": factor.label,
            "group": factor.group,
            "kind": factor.kind,
            "direct_strategy_refs": direct.get(fid, 0),
            "composite_member_refs": composite_members.get(fid, 0),
            "retired": retired,
            "ledger_mentioned": mentioned,
            "covered": bool(
                direct.get(fid, 0)
                or composite_members.get(fid, 0)
                or retired
                or mentioned
            ),
        })

    remaining = [item for item in items if not item["covered"]]
    payload = {
        "purpose": "R107 factor/strategy coverage audit",
        "strategy_count": len(strategy_ids),
        "factor_count": len(items),
        "directly_used_factor_count": sum(1 for item in items if item["direct_strategy_refs"]),
        "composite_member_factor_count": sum(1 for item in items if item["composite_member_refs"]),
        "retired_factor_count": sum(1 for item in items if item["retired"]),
        "ledger_mentioned_factor_count": sum(1 for item in items if item["ledger_mentioned"]),
        "remaining_uncovered_count": len(remaining),
        "strategies": strategy_ids,
        "direct_strategy_refs": dict(direct.most_common()),
        "composite_member_refs": dict(composite_members.most_common()),
        "remaining_uncovered": remaining,
        "factors": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "strategies": payload["strategy_count"],
        "factors": payload["factor_count"],
        "directly_used": payload["directly_used_factor_count"],
        "composite_members": payload["composite_member_factor_count"],
        "retired": payload["retired_factor_count"],
        "ledger_mentioned": payload["ledger_mentioned_factor_count"],
        "remaining_uncovered": payload["remaining_uncovered_count"],
        "remaining_ids": [item["id"] for item in remaining],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
