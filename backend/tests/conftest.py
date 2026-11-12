"""Shared test preparation for research-contract and runtime-data tests."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

# These tests compare the fingerprints/manifests of the complete local research
# snapshot. The snapshot is intentionally too large for git (raw daily bars,
# alternative-data partitions and prior research caches), so a clean CI checkout
# must skip only these integration checks instead of failing on absent fixtures.
_RESEARCH_SNAPSHOT_TESTS = frozenset({
    "test_preflight_and_backtest_share_frozen_r360_inputs",
    "test_r362_preflight_and_backtest_share_the_frozen_contract",
    "test_r363_preflight_and_backtest_share_frozen_input_manifest",
    "test_r364_preflight_and_runner_share_frozen_manifest",
    "test_r365_preflight_and_backtest_share_the_frozen_manifest",
    "test_r366_manifest_hashes_the_inputs_and_rejects_r362_control_substitution",
    "test_r367_preflight_and_runner_use_the_same_frozen_manifest",
    "test_r368_manifest_and_runner_preflight_agree",
    "test_audit_reconstructs_r360_and_separates_tested_from_untested_parameters",
})

_RESEARCH_SNAPSHOT_INPUTS = (
    DATA / "kline_daily",
    DATA / "kline_daily_enriched",
    DATA / "research/r40_engine_data/kline_daily_enriched",
    DATA / "ext_data/alt_hs300/timeseries",
    DATA / "ext_data/ext_hy_ths/part.parquet",
    DATA / "regime_history/part.parquet",
    DATA / "research/r78_daily_membership/daily_snapshots.parquet",
    DATA / "research/r157_membership/monthly_snapshots.parquet",
    DATA / "instruments/instruments.parquet",
    DATA / "research/r331_defensive_assets",
    DATA / "research/r344_cache",
    DATA / "research/r359_v2_cache",
)


@pytest.fixture(scope="session", autouse=True)
def seed_default_runtime_signals() -> None:
    """Mirror application startup for tests that resolve built-in signal IDs."""
    from app.strategy.custom_signals import seed_if_empty

    seed_if_empty(DATA)


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    del config
    missing = [path for path in _RESEARCH_SNAPSHOT_INPUTS if not path.exists()]
    if not missing:
        return
    relative = ", ".join(path.relative_to(ROOT).as_posix() for path in missing[:3])
    suffix = "..." if len(missing) > 3 else ""
    reason = f"完整研究快照不在当前检出中，跳过集成校验: {relative}{suffix}"
    skip = pytest.mark.skip(reason=reason)
    for item in items:
        if item.name in _RESEARCH_SNAPSHOT_TESTS:
            item.add_marker(skip)
