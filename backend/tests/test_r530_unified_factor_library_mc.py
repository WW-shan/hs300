from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r530_unified_factor_library_mc.py"


def _module():
    spec = importlib.util.spec_from_file_location("r530_unified_factor_library_mc_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_contract_type_and_preregistration() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "statistical_screen"' in text
    assert "r530_unified_factor_library_mc_preregistration.json" in text


def test_recommended_candidates_are_frozen_and_exactly_fifteen() -> None:
    module = _module()
    candidates = module.load_recommended_candidates(ROOT)
    assert len(candidates) == 15
    assert len({candidate.family_id for candidate in candidates}) == 15
    for candidate in candidates:
        assert candidate.parameter == candidate.source_recommended_parameter
        assert candidate.parameter in candidate.family["parameter_grid"]
        assert candidate.source_result_sha256


def test_r526_membership_is_daily_three_hundred() -> None:
    module = _module()
    membership = module.load_pit_membership(ROOT)
    assert membership.height == 424200
    counts = membership.group_by("date").len()["len"].unique().to_list()
    assert counts == [300]
    assert membership["date"].min().isoformat() == "2020-12-31"
    assert membership["date"].max().isoformat() == "2026-11-05"


def test_industry_asof_never_uses_future_effective_date() -> None:
    import polars as pl

    module = _module()
    panel = pl.DataFrame(
        {
            "symbol": ["A", "A", "A"],
            "date": [module.date(2024, 1, 1), module.date(2024, 1, 3), module.date(2024, 1, 5)],
        }
    )
    industry = pl.DataFrame(
        {
            "symbol": ["A", "A"],
            "effective_date": [module.date(2024, 1, 2), module.date(2024, 1, 4)],
            "industry_pit": ["old", "new"],
        }
    )
    out = module.map_industry_asof(panel, industry)
    assert out["industry_l1"].to_list() == [None, "old", "new"]


def test_library_census_has_all_registered_factors_and_families() -> None:
    module = _module()
    census = module.library_census(ROOT)
    assert census["factor_count"] == 235
    assert census["window_family_count"] == 62
    assert census["threshold_family_count"] == 23
    assert census["parameter_trial_count"] == 472
    assert census["prior_recommended_candidate_count"] == 15


def test_committed_r530_result_is_bound_to_preregistration_and_no_execution() -> None:
    import hashlib
    import json

    root = Path(__file__).resolve().parents[2]
    research = root / "data/factor_research/research"
    prereg = research / "r530_unified_factor_library_mc_preregistration.json"
    result = research / "r530_unified_factor_library_mc_result.json"
    markdown = research / "r530_unified_factor_library_mc_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    result_payload = json.loads(result.read_text(encoding="utf-8"))
    assert result_payload["research_type"] == "statistical_screen"
    assert result_payload["preregistration_sha256"] == hashlib.sha256(prereg.read_bytes()).hexdigest()
    assert result_payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert result_payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
    candidates = result_payload["metrics"]["candidates"]
    assert len(candidates) == 15
    assert sum(row["conclusion"] == "admitted_for_research" for row in candidates) == 11
    assert sum(row["conclusion"] == "rejected" for row in candidates) == 4
