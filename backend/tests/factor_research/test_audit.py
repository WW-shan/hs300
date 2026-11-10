from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.factor_research.audit import AuditService
from app.factor_research.models import FactorSetVersion, SetMember, SetPreflight
from app.factor_research.policies import load_policy
from app.factor_research.scoring_adapter import factor_set_to_scoring


def _panel() -> pl.DataFrame:
    return pl.DataFrame({
        "date": [date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 2)],
        "symbol": ["a", "b", "a", "b"],
        "factor_x": [1.0, 2.0, 2.0, 3.0],
        "momentum_20d": [0.1, 0.2, 0.2, 0.3],
    })


def test_audit_detects_stale_and_cross_section(tmp_path: Path) -> None:
    policy = load_policy(None)
    result = AuditService(tmp_path, policy).audit_panel(_panel(), ["factor_x"])
    assert result.factors["factor_x"].cross_section_type == "stock_cross_sectional"
    assert result.factors["factor_x"].latest_date == "2026-11-02"
    assert result.audit_id.startswith("sha256:")


def test_missing_factor_is_unknown_not_empty_pass(tmp_path: Path) -> None:
    policy = load_policy(None)
    result = AuditService(tmp_path, policy).audit_panel(_panel(), ["missing"])
    assert result.factors["missing"].available is False
    assert result.findings["missing"]["integrity"] == "unknown"


def test_five_x_is_diagnostic_only() -> None:
    policy = load_policy(None)
    assert policy.cost.diagnostic_5x.hard_rejection is False


def test_scoring_adapter_uses_active_alpha_only() -> None:
    version = FactorSetVersion(
        set_id="core_v1",
        version=1,
        created_at="2026-11-10T00:00:00Z",
        catalog_snapshot_id="sha256:a",
        members=[
            SetMember(
                factor_id="momentum_20d", factor_version=1, definition_fingerprint="x",
                role="alpha", membership_status="active", direction="high", weight=1.0,
            ),
            SetMember(
                factor_id="rsi_14", factor_version=1, definition_fingerprint="y",
                role="alpha", membership_status="watch", direction="high", weight=None,
            ),
        ],
        preflight=SetPreflight(catalog_snapshot_id="sha256:a"),
        content_sha256="sha256:b",
    )
    view = factor_set_to_scoring(version)
    assert view.scoring == {"momentum_20d": 1.0}
    assert view.research_only is False


def test_audit_manifest_can_bind_active_gate(tmp_path: Path) -> None:
    from app.factor_research.audit import load_audit

    policy = load_policy(None)
    gate = {
        "integrity": "pass",
        "incremental": "positive",
        "parameter_status": "pass",
        "investability": "paper_ready",
        "source_completeness": "complete",
    }
    result = AuditService(tmp_path, policy).audit_panel(
        _panel(), ["factor_x"], set_id="core_v1", set_version=1, active_gate=gate,
    )
    loaded = load_audit(tmp_path, result.audit_id)
    assert loaded is not None
    assert loaded["manifest"]["active_gate"] == gate
    assert loaded["manifest"]["set_id"] == "core_v1"
