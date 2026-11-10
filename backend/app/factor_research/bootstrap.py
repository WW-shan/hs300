"""Bootstrap the initial research pools and watch-only core set."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from app.factor_research.catalog import build_catalog, save_catalog
from app.factor_research.ledger import ResearchLedger
from app.factor_research.models import FactorResearchModel
from app.factor_research.pools import PoolStore
from app.factor_research.sets import FactorSetStore
from app.factors import store


class BootstrapResult(FactorResearchModel):
    set_id: str
    state: str
    active_version: int | None
    snapshot_id: str
    ledger_records: int


CORE_POOLS: dict[str, str] = {
    "financial_v1": "财务质量、价值、成长和安全字段",
    "sentiment_flow_v1": "个股情绪、北向、融资和成交结构字段",
    "industry_v1": "行业动量、轮动、主线和行业内选股字段",
    "regime_v1": "市场情绪、宽度、周期和宏观条件字段",
}


def _default_core_members() -> list[dict[str, Any]]:
    return [
        {
            "factor_id": "ext_alt_hs300_stock_rel_industry_20d",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 0.35,
            "rationale": "R360 行业相对强弱核心腿",
        },
        {
            "factor_id": "ext_alt_hs300_industry_mom_20d",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 0.15,
            "rationale": "R360 行业动量腿",
        },
        {
            "factor_id": "uf_quality_value_defensive",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 0.30,
            "rationale": "R360 质量价值防守腿",
        },
        {
            "factor_id": "uf_cash_quality",
            "role": "alpha",
            "membership_status": "watch",
            "direction": "high",
            "weight": 0.20,
            "rationale": "R360 现金质量腿",
        },
        {
            "factor_id": "uf_industry_relative_strength_change_5d",
            "role": "exit_signal",
            "membership_status": "watch",
            "direction": "low",
            "weight": None,
            "rationale": "行业相对强弱转负/走弱退出",
        },
    ]


def _ensure_pool(pools: PoolStore, pool_id: str, purpose: str, members: Iterable[Mapping[str, Any]]) -> None:
    current = pools.get(pool_id)
    if current is not None:
        return
    pools.create_revision(
        pool_id,
        members,
        0,
        f"初始化 {pool_id} 研究池",
        label=pool_id,
        purpose=purpose,
    )


def bootstrap_core_research(
    data_dir: Path,
    *,
    core_members: Iterable[Mapping[str, Any]] | None = None,
) -> BootstrapResult:
    data_dir = Path(data_dir)
    store.load_into_registry(data_dir)
    catalog = build_catalog(data_dir)
    save_catalog(data_dir, catalog)
    ledger = ResearchLedger(data_dir)
    if not any(
        item.operation == "catalog_published" and item.catalog_snapshot_id == catalog.snapshot_id
        for item in ledger.records()
    ):
        ledger.append(
            "catalog_published",
            payload={"catalog_snapshot_id": catalog.snapshot_id, "reason": "bootstrap catalog"},
        )

    pools = PoolStore(data_dir, ledger=ledger)
    _ensure_pool(pools, "core_v1", "核心研究候选集合", core_members or _default_core_members())
    for pool_id, purpose in CORE_POOLS.items():
        _ensure_pool(pools, pool_id, purpose, [])

    sets = FactorSetStore(data_dir, ledger=ledger, catalog=catalog)
    head = sets.head("core_v1")
    if head is None:
        members = list(core_members or _default_core_members())
        version = sets.create_version(
            "core_v1",
            members,
            None,
            None,
            "初始化 core_v1 研究版本（watch only）",
        )
        head = sets.set_head(
            "core_v1",
            "watch",
            version.version,
            None,
            "R360 仅进入纸面复核，不自动激活",
        )
    elif head.state == "active":
        raise ValueError("refusing to bootstrap over an existing active core_v1 head")

    return BootstrapResult(
        set_id="core_v1",
        state=head.state,
        active_version=head.active_version,
        snapshot_id=catalog.snapshot_id,
        ledger_records=len(ledger.records()),
    )
