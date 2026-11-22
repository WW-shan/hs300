from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r527_unified_pit_family_comparison_preregistration.json"
OUTPUT = E / "r527_unified_pit_family_comparison_2026-11-23.json"
RESULT = E / "r527_unified_pit_family_comparison_2026-11-23.md"
RUNNER = ROOT / "scripts/research_r527_unified_pit_family_comparison.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module():
    spec = importlib.util.spec_from_file_location("r527_runner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r527_preregistration_is_frozen_and_uses_confirmed_r506_weights() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R527"
    assert payload["families"]["hold30"]["portfolio_parameters"]["c_satellite_cap"] == 0.13
    weights = payload["families"]["five_leg"]["weights"]
    assert weights == {
        "ext_alt_hs300_cash_flow_stability_4q_low": 0.225,
        "ext_alt_hs300_dividend_yield": 0.225,
        "ext_alt_hs300_fund_holding_ratio_low": 0.225,
        "ext_alt_hs300_idio_vol_40d_low": 0.225,
        "value_quality": 0.10,
    }
    assert payload["families"]["five_leg"]["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["gates"]["shared_industry_neutralization_mapping"] is False
    assert payload["output_policy"]["active_version"] is None
    assert payload["output_policy"]["production_enabled"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]


def test_r527_sources_exist_and_have_sidecars() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    for relative in payload["sources"].values():
        path = ROOT / relative
        assert path.exists(), path
        assert path.with_name(path.name + ".sha256").exists(), path


def test_r527_outputs_when_present_record_real_family_runs_and_no_execution() -> None:
    if not OUTPUT.exists() or not RESULT.exists():
        return
    for path in (OUTPUT, RESULT):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["comparison"]["same_membership_for_both"] is True
    assert payload["comparison"]["same_industry_neutralization_mapping"] is False
    assert payload["families"]["hold30"]["run"]["curve_points"] >= 2
    assert payload["families"]["five_leg"]["run"]["curve_points"] >= 2
    assert payload["decision"]["family_runs_complete"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
    assert payload["comparison"]["winner"] is None
    assert payload["families"]["hold30"]["run"]["cost_runs"]["5x"]["annual"] > 0
    assert payload["families"]["five_leg"]["run"]["cost_runs"]["5x"]["annual_return"] > 0


def test_r527_industry_pit_dates_are_applied_without_lookahead() -> None:
    import polars as pl
    module = _module()
    industry = pl.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "effective_date": [date(2020, 1, 1), date(2022, 1, 1), date(2020, 1, 1)],
            "industry_pit": ["11", "22", "33"],
        }
    )
    panel = pl.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "date": [date(2021, 12, 31), date(2022, 1, 3), date(2021, 12, 31)],
        }
    )
    out = module.map_industry_asof(panel, industry).sort(["symbol", "date"])
    assert out["industry_l1"].to_list() == ["11", "22", "33"]


def test_r527_bundle_membership_is_the_only_membership_input() -> None:
    module = _module()
    membership = module.bundle_membership()
    assert membership.columns == ["effective_date", "symbol"]
    assert membership.height > 0
    counts = membership.group_by("effective_date").len()["len"].unique().to_list()
    assert counts == [300]


def test_r527_factor_registry_is_restored_after_family_run() -> None:
    module = _module()
    from app.factors.registry import FactorSpec, register_factor

    snapshot = module.snapshot_factor_registry()
    probe = FactorSpec(
        id="uf_r527_isolation_probe",
        label="R527 isolation probe",
        group="test",
        formula_text="close",
        kind="base",
        warmup_bars=1,
    )
    try:
        register_factor(probe)
        assert module.snapshot_factor_registry()[0].get(probe.id) is not None
    finally:
        module.restore_factor_registry(snapshot)
    assert probe.id not in module.snapshot_factor_registry()[0]
