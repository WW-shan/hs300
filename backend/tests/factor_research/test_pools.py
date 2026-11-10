from __future__ import annotations

from pathlib import Path

import pytest

from app.factor_research.pools import PoolConflictError, PoolStore


def test_pool_revision_is_append_only(tmp_path: Path) -> None:
    store = PoolStore(tmp_path)
    first = store.create_revision("core_v1", [], 0, "初始化")
    second = store.create_revision("core_v1", [], 1, "第二次")
    assert first.revision == 1
    assert second.parent_revision == 1
    assert store.get("core_v1", 1).change_reason == "初始化"
    assert store.get("core_v1").revision == 2


def test_pool_rejects_stale_expected_revision(tmp_path: Path) -> None:
    store = PoolStore(tmp_path)
    store.create_revision("core_v1", [], 0, "初始化")
    with pytest.raises(PoolConflictError):
        store.create_revision("core_v1", [], 0, "旧页面提交")


def test_pool_rejects_dangling_member(tmp_path: Path) -> None:
    store = PoolStore(tmp_path)
    with pytest.raises(ValueError):
        store.create_revision("core_v1", [{"factor_id": "not_a_factor"}], 0, "坏引用")
