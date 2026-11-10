from __future__ import annotations

import json
from datetime import date, timedelta
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


def _taxonomy(**updates: object) -> dict[str, dict[str, object]]:
    base = {
        "data_class": "price",
        "cross_section_type": "stock_cross_sectional",
        "pit_hint": "not_applicable",
        "zero_is_missing": False,
        "classification_confidence": 0.9,
    }
    return {"factor_x": {**base, **updates}}


def test_audit_detects_stale_and_cross_section(tmp_path: Path) -> None:
    policy = load_policy(None)
    result = AuditService(tmp_path, policy, taxonomy=_taxonomy()).audit_panel(_panel(), ["factor_x"])
    assert result.factors["factor_x"].cross_section_type == "stock_cross_sectional"
    assert result.factors["factor_x"].latest_date == "2026-11-02"
    assert result.audit_id.startswith("sha256:")


def test_missing_factor_is_unknown_not_empty_pass(tmp_path: Path) -> None:
    policy = load_policy(None)
    result = AuditService(tmp_path, policy, taxonomy=_taxonomy()).audit_panel(_panel(), ["missing"])
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
    result = AuditService(tmp_path, policy, taxonomy=_taxonomy()).audit_panel(
        _panel(), ["factor_x"], set_id="core_v1", set_version=1, active_gate=gate,
    )
    loaded = load_audit(tmp_path, result.audit_id)
    assert loaded is not None
    assert loaded["manifest"]["active_gate"] == gate
    assert loaded["manifest"]["set_id"] == "core_v1"


def test_staleness_counts_panel_trading_dates_and_uses_data_class_threshold(tmp_path: Path) -> None:
    dates = [date(2025, 1, 2) + timedelta(days=index * 40) for index in range(5)]
    panel = pl.DataFrame({
        "date": [item for item in dates for _ in range(2)],
        "symbol": ["a", "b"] * len(dates),
        "factor_x": [1.0, 2.0, None, None, None, None, None, None, None, None],
        "financial_x": [1.0, 2.0, None, None, None, None, None, None, None, None],
    })
    taxonomy = _taxonomy()
    taxonomy["financial_x"] = {
        **taxonomy["factor_x"],
        "data_class": "financial",
        "pit_hint": "declared",
    }
    policy = load_policy(None).model_copy(update={
        "staleness_trading_days": {**load_policy(None).staleness_trading_days, "price": 2},
    })
    result = AuditService(tmp_path, policy, taxonomy=taxonomy).audit_panel(
        panel, ["factor_x", "financial_x"]
    )
    assert result.factors["factor_x"].staleness_days == 4
    assert "latest_value_is_stale" in result.factors["factor_x"].reasons
    assert result.factors["financial_x"].staleness_days == 4
    assert "latest_value_is_stale" not in result.factors["financial_x"].reasons


def test_zero_filled_event_uses_latest_informative_value(tmp_path: Path) -> None:
    panel = pl.DataFrame({
        "date": [date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 3)],
        "symbol": ["a", "b", "a"],
        "event_flag": [1.0, 0.0, 0.0],
    })
    taxonomy = {
        "event_flag": {
            **_taxonomy()["factor_x"],
            "data_class": "flow",
            "cross_section_type": "event_sparse",
            "zero_is_missing": True,
        }
    }
    result = AuditService(tmp_path, load_policy(None), taxonomy=taxonomy).audit_panel(
        panel, ["event_flag"]
    )
    record = result.factors["event_flag"]
    assert record.latest_non_null_date == "2026-11-03"
    assert record.latest_informative_date == "2026-11-01"
    assert record.staleness_days == 2
    assert record.informative_ratio == 0.333333


def test_taxonomy_staleness_override_supports_monthly_flow_sources(tmp_path: Path) -> None:
    dates = [date(2026, 10, 1) + timedelta(days=index) for index in range(12)]
    panel = pl.DataFrame({
        "date": dates,
        "symbol": ["a"] * len(dates),
        "monthly_flow": [1.0, *([None] * (len(dates) - 1))],
    })
    taxonomy = {
        "monthly_flow": {
            **_taxonomy()["factor_x"],
            "data_class": "flow",
            "staleness_days_override": 45,
        }
    }
    result = AuditService(tmp_path, load_policy(None), taxonomy=taxonomy).audit_panel(
        panel, ["monthly_flow"]
    )

    assert "latest_value_is_stale" not in result.factors["monthly_flow"].reasons


def test_industry_taxonomy_is_preserved_and_market_mismatch_is_reported(tmp_path: Path) -> None:
    panel = pl.DataFrame({
        "date": [date(2026, 11, 1)] * 6,
        "symbol": ["a", "b", "c", "d", "e", "f"],
        "industry_x": [1.0, 1.0, 2.0, 2.0, 3.0, 3.0],
        "market_x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    taxonomy = {
        "industry_x": {**_taxonomy()["factor_x"], "cross_section_type": "industry_cross_sectional"},
        "market_x": {**_taxonomy()["factor_x"], "cross_section_type": "market_level"},
    }
    result = AuditService(tmp_path, load_policy(None), taxonomy=taxonomy).audit_panel(
        panel, ["industry_x", "market_x"]
    )
    assert result.factors["industry_x"].cross_section_type == "industry_cross_sectional"
    assert result.factors["market_x"].cross_section_type == "market_level"
    assert "classification_mismatch" in result.factors["market_x"].reasons


def test_ext_data_audit_reads_date_partitions_without_materializing_panel(tmp_path: Path) -> None:
    from app.factor_research.audit import load_audit

    timeseries = tmp_path / "ext_data" / "demo" / "timeseries"
    for day, values in {
        "2026-11-03": [1.0, 2.0],
        "2026-11-04": [2.0, None],
    }.items():
        path = timeseries / f"date={day}" / "part.parquet"
        path.parent.mkdir(parents=True)
        pl.DataFrame({"symbol": ["a", "b"], "event": values}).write_parquet(path)
    config = {
        "id": "demo",
        "label": "demo",
        "mode": "timeseries",
        "fields": [{"name": "event", "dtype": "float", "label": "event"}],
    }
    (tmp_path / "ext_data" / "demo" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    taxonomy = {
        "ext_demo_event": {
            **_taxonomy()["factor_x"],
            "data_class": "flow",
            "cross_section_type": "event_sparse",
            "zero_is_missing": True,
        }
    }
    result = AuditService(tmp_path, load_policy(None), taxonomy=taxonomy).audit_ext_data(
        "demo", ["ext_demo_event"]
    )
    record = result.factors["ext_demo_event"]
    assert record.available is True
    assert record.valid_dates == 2
    assert record.latest_non_null_date == "2026-11-04"
    assert record.latest_informative_date == "2026-11-04"
    loaded = load_audit(tmp_path, result.audit_id)
    assert loaded is not None
    assert loaded["manifest"]["input_fingerprints"]["ext_data"]["config_path"] == (
        "ext_data/demo/config.json"
    )
