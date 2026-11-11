from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.factor_research.models import CatalogSnapshot, FactorRecord, SourceLocator
from app.factor_research.parameters import (
    ParameterFamilyRegistry,
    build_classification_payload,
    classify_factor_parameters,
    load_parameter_registry,
    validate_parameter_registry,
)


def _factor(factor_id: str) -> FactorRecord:
    return FactorRecord(
        factor_id=factor_id,
        factor_version=1,
        label=factor_id,
        group="test",
        kind="base",
        formula_text=factor_id,
        source=SourceLocator(type="builtin", module="test"),
        definition_fingerprint=f"fingerprint-{factor_id}",
    )


def _registry_payload(
    *,
    family_id: str = "idio_vol",
    member_factor_ids: list[str] | None = None,
    parameter_grid: list[int] | None = None,
    current_parameter: int = 20,
    status: str = "tunable",
) -> dict:
    return {
        "schema_version": 1,
        "families": [
            {
                "family_id": family_id,
                "label": "Residual volatility",
                "data_class": "price",
                "cross_section_type": "stock_cross_sectional",
                "direction": "high",
                "evaluator": "residual_volatility",
                "parameter_name": "window",
                "parameter_grid": parameter_grid or [20, 40, 60],
                "current_parameter": current_parameter,
                "minimum_history": 240,
                "neutralization": ["industry", "size"],
                "member_factor_ids": member_factor_ids or ["idio_vol_20"],
                "status": status,
                "evidence_refs": [],
            }
        ],
        "unclassified_factor_ids": [],
        "blocked_factor_ids": [],
    }


def test_registry_rejects_duplicate_family_member_bad_grid_and_missing_factor() -> None:
    payload = _registry_payload(member_factor_ids=["idio_vol_20"], parameter_grid=[60, 20])
    payload["families"].append(
        {
            **payload["families"][0],
            "family_id": "idio_vol",
            "member_factor_ids": ["idio_vol_20", "missing_factor"],
        }
    )
    registry = ParameterFamilyRegistry.model_validate(payload)

    errors = validate_parameter_registry(registry, {"idio_vol_20"})

    assert any("duplicate family" in error for error in errors)
    assert any("duplicate member" in error for error in errors)
    assert any("ascending" in error for error in errors)
    assert any("missing factor" in error for error in errors)


def test_registry_rejects_current_parameter_outside_grid_and_nonpositive_grid() -> None:
    registry = ParameterFamilyRegistry.model_validate(
        _registry_payload(parameter_grid=[20, 60], current_parameter=40)
    )

    errors = validate_parameter_registry(registry, {"idio_vol_20"})
    assert any("current parameter" in error for error in errors)

    with pytest.raises(ValidationError):
        ParameterFamilyRegistry.model_validate(
            _registry_payload(parameter_grid=[0, 20], current_parameter=20)
        )


def test_classification_marks_members_grouped_unregistered_windows_unclassified_and_fixed() -> None:
    factors = [
        _factor("idio_vol_20"),
        _factor("momentum_999d"),
        _factor("ma5_bias"),
        _factor("roe_latest"),
    ]
    registry = ParameterFamilyRegistry.model_validate(_registry_payload())

    rows = classify_factor_parameters(factors, registry)
    by_id = {row.factor_id: row for row in rows}

    assert by_id["idio_vol_20"].status == "grouped"
    assert by_id["idio_vol_20"].family_id == "idio_vol"
    assert by_id["momentum_999d"].status == "unclassified"
    assert by_id["ma5_bias"].status == "unclassified"
    assert by_id["roe_latest"].status == "fixed"
    assert [row.factor_id for row in rows] == sorted(by_id)


def test_blocked_family_members_fail_closed() -> None:
    factors = [_factor("idio_vol_20")]
    registry = ParameterFamilyRegistry.model_validate(
        _registry_payload(status="blocked")
    )

    row = classify_factor_parameters(factors, registry)[0]

    assert row.status == "blocked"
    assert row.family_id == "idio_vol"


def test_load_parameter_registry_uses_factor_research_layout(tmp_path: Path) -> None:
    path = tmp_path / "factor_research" / "parameters" / "families.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        """{
  "schema_version": 1,
  "families": [{
    "family_id": "idio_vol",
    "label": "Residual volatility",
    "data_class": "price",
    "cross_section_type": "stock_cross_sectional",
    "direction": "high",
    "evaluator": "residual_volatility",
    "parameter_name": "window",
    "parameter_grid": [20, 40, 60],
    "current_parameter": 20,
    "minimum_history": 240,
    "neutralization": ["industry", "size"],
    "member_factor_ids": ["idio_vol_20"],
    "status": "tunable",
    "evidence_refs": []
  }],
  "unclassified_factor_ids": [],
  "blocked_factor_ids": []
}
""",
        encoding="utf-8",
    )

    registry = load_parameter_registry(tmp_path)

    assert registry.families[0].family_id == "idio_vol"


def test_classification_payload_is_deterministic_and_counts_every_factor() -> None:
    factors = (_factor("idio_vol_20"), _factor("momentum_999d"), _factor("roe_latest"))
    factor_rows = list(factors)
    catalog = CatalogSnapshot(
        snapshot_id="sha256:catalog",
        generated_at="2026-11-11T00:00:00Z",
        registry_fingerprint="sha256:registry",
        factor_count=len(factor_rows),
        factors=factor_rows,
    )
    registry = ParameterFamilyRegistry.model_validate(_registry_payload())

    payload = build_classification_payload(
        catalog,
        registry,
        generated_at="2026-11-11T01:00:00Z",
    )
    repeat = build_classification_payload(
        catalog,
        registry,
        generated_at="2026-11-11T02:00:00Z",
    )

    assert payload["report_id"] == repeat["report_id"]
    assert payload["counts"] == {
        "total": 3,
        "tunable": 0,
        "fixed": 1,
        "grouped": 1,
        "blocked": 0,
        "unclassified": 1,
    }
    assert payload["catalog"]["snapshot_id"] == "sha256:catalog"
    assert len(payload["factors"]) == 3


def test_p1_manifest_covers_eight_families_and_all_catalog_members() -> None:
    from app.factor_research.catalog import load_latest_catalog

    root = Path(__file__).resolve().parents[3]
    catalog = load_latest_catalog(root / "data")
    assert catalog is not None
    registry = load_parameter_registry(root / "data")

    assert catalog.factor_count == 223
    assert len(catalog.factors) == 223
    assert {
        "idio_vol",
        "cs_spread",
        "vol_of_vol",
        "market_down_beta",
        "price_delay",
        "trend_efficiency",
        "risk_adjusted_momentum",
        "beta_asymmetry",
    }.issubset({family.family_id for family in registry.families})
    assert validate_parameter_registry(registry, {item.factor_id for item in catalog.factors}) == []

    rows = classify_factor_parameters(catalog.factors, registry)
    assert len(rows) == 223
    assert all(
        row.status == "grouped"
        for row in rows
        if row.factor_id in {
            factor_id
            for family in registry.families
            for factor_id in family.member_factor_ids
        }
    )


def test_explicit_fixed_factor_overrides_numeric_window_detection() -> None:
    payload = _registry_payload()
    payload["fixed_factor_ids"] = ["momentum_999d"]
    registry = ParameterFamilyRegistry.model_validate(payload)

    rows = classify_factor_parameters(
        [_factor("idio_vol_20"), _factor("momentum_999d")], registry
    )
    row = next(item for item in rows if item.factor_id == "momentum_999d")

    assert row.status == "fixed"
    assert row.reason == "explicitly fixed by parameter registry"


def test_registry_rejects_reason_for_unknown_factor() -> None:
    payload = _registry_payload()
    payload["fixed_factor_reasons"] = {"missing": "not an audited horizon"}
    registry = ParameterFamilyRegistry.model_validate(payload)

    errors = validate_parameter_registry(registry, {"idio_vol_20"})

    assert "missing factor missing in fixed_factor_reasons" in errors


def test_classification_uses_explicit_fixed_and_blocked_reasons() -> None:
    payload = _registry_payload()
    payload["fixed_factor_ids"] = ["roe_latest"]
    payload["fixed_factor_reasons"] = {
        "roe_latest": "single audited structural horizon",
    }
    payload["blocked_factor_ids"] = ["northbound_20d"]
    payload["blocked_factor_reasons"] = {
        "northbound_20d": "stale: last non-null 2024-08-16",
    }
    registry = ParameterFamilyRegistry.model_validate(payload)

    rows = classify_factor_parameters(
        [
            _factor("idio_vol_20"),
            _factor("roe_latest"),
            _factor("northbound_20d"),
        ],
        registry,
    )
    by_id = {row.factor_id: row for row in rows}

    assert by_id["roe_latest"].status == "fixed"
    assert by_id["roe_latest"].reason == "single audited structural horizon"
    assert by_id["northbound_20d"].status == "blocked"
    assert by_id["northbound_20d"].reason == "stale: last non-null 2024-08-16"


def test_fixed_factor_cannot_overlap_family_or_blocked_member() -> None:
    payload = _registry_payload()
    payload["fixed_factor_ids"] = ["idio_vol_20"]
    payload["blocked_factor_ids"] = ["idio_vol_20"]
    registry = ParameterFamilyRegistry.model_validate(payload)

    errors = validate_parameter_registry(registry, {"idio_vol_20"})

    assert any("both a family member and fixed" in error for error in errors)
    assert any("both fixed and blocked" in error for error in errors)


def test_p2_manifest_leaves_no_builtin_factor_unclassified() -> None:
    from app.factor_research.catalog import load_latest_catalog

    root = Path(__file__).resolve().parents[3]
    catalog = load_latest_catalog(root / "data")
    assert catalog is not None
    registry = load_parameter_registry(root / "data")
    expected_p2_families = {
        "short_momentum_reversal",
        "medium_momentum",
        "short_ma_reversal",
        "medium_ma_trend",
        "short_ema_reversal",
        "medium_ema_trend",
        "rsi_reversal",
        "annual_volatility",
        "downside_volatility",
        "atr_risk",
        "bollinger_position",
        "bollinger_width",
        "price_position",
        "distance_to_high",
        "distance_from_low",
        "amihud",
        "amount_level",
        "turnover_level",
        "turnover_stability",
        "turnover_crowding",
        "volume_spike",
        "turnover_spike",
        "amount_spike",
        "obv_trend",
        "volume_price_corr",
        "limit_up_gene",
        "max_return",
        "return_skew",
        "up_day_ratio",
        "overnight_return",
        "intraday_return",
    }
    assert expected_p2_families.issubset(
        {family.family_id for family in registry.families}
    )
    assert validate_parameter_registry(
        registry, {factor.factor_id for factor in catalog.factors}
    ) == []

    rows = classify_factor_parameters(catalog.factors, registry)
    by_id = {row.factor_id: row for row in rows}
    builtin_ids = {
        factor.factor_id
        for factor in catalog.factors
        if factor.source.type == "builtin"
    }
    assert not [
        factor_id
        for factor_id in builtin_ids
        if by_id[factor_id].status == "unclassified"
    ]


def test_p3_manifest_classifies_every_remaining_window_factor() -> None:
    from app.factor_research.catalog import load_latest_catalog

    root = Path(__file__).resolve().parents[3]
    catalog = load_latest_catalog(root / "data")
    assert catalog is not None
    registry = load_parameter_registry(root / "data")
    expected_p3_families = {
        "industry_momentum",
        "industry_rps",
        "industry_breadth",
        "stock_rel_industry_strength",
        "insider_buy_event",
        "insider_net_buy_event",
        "insider_sell_event_low",
        "lhb_net_buy_event",
        "lhb_inst_net_buy_event",
        "lhb_event_count_low",
        "repurchase_event_decay",
        "block_trade_ratio_event",
        "block_premium_event",
        "analyst_rating_level",
        "analyst_rating_change",
        "bond_futures_momentum",
        "bond_futures_trend_momentum",
        "hsi_momentum",
        "hscei_momentum",
        "hsi_relative_hs300",
        "etf_share_change",
        "qvix_zscore",
        "market_skew",
    }
    assert expected_p3_families.issubset(
        {family.family_id for family in registry.families}
    )
    assert validate_parameter_registry(
        registry, {factor.factor_id for factor in catalog.factors}
    ) == []

    rows = classify_factor_parameters(catalog.factors, registry)
    assert all(row.status != "unclassified" for row in rows)
    assert all(
        row.reason
        for row in rows
        if row.factor_id in set(registry.fixed_factor_ids)
        | set(registry.blocked_factor_ids)
    )


def test_current_family_factor_id_supports_single_unparsed_factor() -> None:
    from app.factor_research.parameters import current_family_factor_id

    payload = _registry_payload(
        family_id="bollinger_position",
        member_factor_ids=["boll_position"],
        parameter_grid=[10, 20, 40],
        current_parameter=20,
    )
    payload["families"][0]["evaluator"] = "bollinger_position"
    registry = ParameterFamilyRegistry.model_validate(payload)

    assert current_family_factor_id(registry.families[0]) == "boll_position"


def test_current_family_factor_id_fails_closed_when_ambiguous() -> None:
    from app.factor_research.parameters import current_family_factor_id

    payload = _registry_payload(
        family_id="ambiguous",
        member_factor_ids=["factor_a", "factor_b"],
        parameter_grid=[10, 20, 40],
        current_parameter=20,
    )
    registry = ParameterFamilyRegistry.model_validate(payload)

    with pytest.raises(ValueError, match="exactly one current member"):
        current_family_factor_id(registry.families[0])
