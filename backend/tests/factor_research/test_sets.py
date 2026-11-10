from __future__ import annotations

from pathlib import Path

import pytest

from app.factor_research.sets import (
    AuditRequiredError,
    FactorSetStore,
    SetConflictError,
)


def test_set_version_cannot_be_overwritten(tmp_path: Path) -> None:
    store = FactorSetStore(tmp_path)
    version = store.create_version("core_v1", [], None, None, "初始化")
    assert version.version == 1
    with pytest.raises(SetConflictError):
        store.create_version("core_v1", [], None, None, "重复")


def test_active_requires_audit_checker(tmp_path: Path) -> None:
    store = FactorSetStore(tmp_path)
    store.create_version(
        "core_v1",
        [{
            "factor_id": "momentum_20d",
            "role": "alpha",
            "membership_status": "active",
            "direction": "high",
            "weight": 1.0,
        }],
        None,
        None,
        "初始化",
    )
    with pytest.raises(AuditRequiredError):
        store.set_head("core_v1", "active", 1, None, "直接激活")


def test_watch_head_is_allowed_without_audit(tmp_path: Path) -> None:
    store = FactorSetStore(tmp_path)
    store.create_version("core_v1", [], None, None, "初始化")
    head = store.set_head("core_v1", "watch", 1, None, "只看不激活")
    assert head.state == "watch"
    assert head.watch_version == 1
    assert head.active_version is None


def test_active_with_fake_audit_is_allowed_for_research_set(tmp_path: Path) -> None:
    store = FactorSetStore(tmp_path)
    store.create_version(
        "core_v1",
        [{
            "factor_id": "momentum_20d",
            "role": "alpha",
            "membership_status": "active",
            "direction": "high",
            "weight": 1.0,
        }],
        None,
        None,
        "初始化",
    )
    head = store.set_head(
        "core_v1",
        "active",
        1,
        None,
        "审计通过",
        audit_checker=lambda _set_id, _version: {
            "integrity": "pass",
            "incremental": "positive",
            "parameter_status": "pass",
            "investability": "paper_ready",
            "source_completeness": "complete",
        },
    )
    assert head.state == "active"
    assert head.active_version == 1


def test_parent_version_must_exist(tmp_path: Path) -> None:
    store = FactorSetStore(tmp_path)
    store.create_version("core_v1", [], None, None, "初始化")
    store.set_head("core_v1", "watch", 1, None, "watch")
    head = store.head("core_v1")
    token = store._head_token(head)
    with pytest.raises(ValueError):
        store.create_version("core_v1", [], 99, token, "坏父版本")
