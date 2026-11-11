from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import polars as pl
import pytest

from app.factors.registry import get_factor

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "research_factor_increment_p5.py"


def _module():
    spec = importlib.util.spec_from_file_location("research_factor_increment_p5", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_candidate_collection_deduplicates_same_economic_meaning() -> None:
    module = _module()

    selected, dedupe = module.collect_candidates(module._load_payloads())

    by_key = {item["candidate_key"]: item for item in selected}
    assert len(selected) == 13
    assert len(dedupe) == 2
    assert "p4_dividend_abs_0015" in by_key
    assert "p4_dividend_q040" not in by_key
    assert "p3_repurchase_decay_p20" in by_key
    assert "p4_repurchase_holding_p20" not in by_key
    assert {
        (item["kept"], item["dropped"])
        for item in dedupe
    } == {
        ("p4_dividend_abs_0015", "p4_dividend_q040"),
        ("p3_repurchase_decay_p20", "p4_repurchase_holding_p20"),
    }


def test_candidate_roles_detect_replacements_and_existing_members() -> None:
    module = _module()
    selected, _ = module.collect_candidates(module._load_payloads())
    current_ids = [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_holder_top10_share_chg_yoy",
        "ext_alt_hs300_idio_vol_40d_low",
        "ext_alt_hs300_margin_chg_1m_low",
        "ext_alt_hs300_ocf_margin_4q",
        "ext_alt_hs300_vol_of_vol_20d_low",
    ]

    module.annotate_candidate_roles(selected, current_ids)
    by_key = {item["candidate_key"]: item for item in selected}

    assert by_key["p1_idio_vol_p20"]["set_role"] == "replacement"
    assert (
        by_key["p1_idio_vol_p20"]["replaces_factor_id"]
        == "ext_alt_hs300_idio_vol_40d_low"
    )
    assert by_key["p4_dividend_abs_0015"]["set_role"] == "replacement"
    assert by_key["p4_margin_q050"]["set_role"] == "replacement"
    assert by_key["p2_amount_p5"]["set_role"] == "new"
    assert by_key["p1_vol_of_vol_p20"]["set_role"] == "already_in_set"


def test_p5_custom_factors_compile_and_remain_draft() -> None:
    module = _module()

    hashes = module._validate_custom_factors(ROOT / "data")
    definitions = module._load_custom_definitions(ROOT / "data")

    assert set(hashes) == set(module.CUSTOM_FACTOR_IDS)
    assert set(definitions) == set(module.CUSTOM_FACTOR_IDS)
    assert all(value.startswith("sha256:") for value in hashes.values())
    assert all(definition["status"] == "draft" for definition in definitions.values())
    assert get_factor("uf_p5_amount5_low") is None


def test_p5_custom_factors_materialize_without_global_registration() -> None:
    module = _module()
    module._validate_custom_factors(ROOT / "data")
    definitions = module._load_custom_definitions(ROOT / "data")
    panel = pl.DataFrame(
        {
            "symbol": ["A"] * 5,
            "date": [f"2025-01-0{index}" for index in range(1, 6)],
            "amount": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )

    materialized = module._materialize_research_factors(
        panel,
        ["uf_p5_amount5_low"],
        definitions,
    )

    assert materialized["uf_p5_amount5_low"].to_list()[-1] == pytest.approx(3e-8)
    assert get_factor("uf_p5_amount5_low") is None


def test_temporary_research_dependencies_do_not_leak() -> None:
    module = _module()
    factor_id = "ext_alt_hs300_test_runtime_dependency"

    with module._temporary_research_dependencies([f"{factor_id} + close"]):
        assert get_factor(factor_id) is not None

    assert get_factor(factor_id) is None


def test_equal_inputs_reject_more_than_eight_factors() -> None:
    module = _module()
    factors = [f"f{i}" for i in range(9)]

    with pytest.raises(ValueError, match=r"1\.\.8"):
        module._equal_inputs(factors, {factor: "high" for factor in factors})


def test_statistical_gate_rejects_correlation_and_unregistered_event_variant() -> None:
    module = _module()
    candidate = {
        "set_role": "new",
        "registered": False,
    }
    result = {
        "directional_delta_mean": 0.01,
        "delta_p_value": 0.01,
        "delta_q_value": 0.02,
        "max_abs_correlation_for_gate": 0.95,
        "n_dates": 1000,
    }

    gate = module.evaluate_statistical_gate(candidate, result)

    assert gate["pass"] is False
    assert "correlation_above_0_90" in gate["reasons"]
    assert "registration_required" in gate["reasons"]


def test_variant_factor_set_replaces_same_slot_or_drops_weakest() -> None:
    module = _module()
    current = [f"f{i}" for i in range(8)]
    replacement = {
        "set_role": "replacement",
        "replaces_factor_id": "f3",
        "factor_id": "candidate",
    }
    new = {
        "set_role": "new",
        "replaces_factor_id": None,
        "factor_id": "candidate",
    }

    assert module._variant_factor_set(replacement, current, "f0")[3] == "candidate"
    assert "f0" not in module._variant_factor_set(new, current, "f0")
    assert module._variant_factor_set(new, current, "f0")[-1] == "candidate"


def test_published_p5_result_keeps_watch_unchanged_at_null_active_version() -> None:
    path = ROOT / "data" / "factor_research" / "research" / "p5_incremental_2026-11-11.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["research_id"] == (
        "sha256:172e4378a449778e692d180761c11fcde237651d54219d7e8c94cd1e03dc818e"
    )
    assert payload["decision"]["status"] == "no_new_watch_version"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["new_watch_version"] is None
    assert payload["base_set"]["set_id"] == "multi_dimension_v1"
    amount = next(
        item for item in payload["candidates"] if item["candidate_key"] == "p2_amount_p5"
    )
    assert amount["statistical_gate"]["pass"] is True
    assert amount["portfolio_gate"]["pass"] is False
    assert amount["portfolio_gate"]["checks"]["full_sharpe_within_tolerance"] is False
