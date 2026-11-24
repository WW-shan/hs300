from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r532_factor_library_core_stability.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "r532_factor_library_core_stability_test", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_contract_type_and_r531_source() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "statistical_screen"' in text
    assert "r532_factor_library_core_stability_preregistration.json" in text
    assert "r531_factor_library_core_result.json" in text


def test_r532_loads_exact_six_frozen_r531_representatives() -> None:
    module = _module()
    representatives, payload = module.load_frozen_core(ROOT)
    assert payload["conclusion"] == "admitted_for_research"
    assert len(representatives) == 6
    assert {row["family_id"] for row in representatives} == {
        "amount_level",
        "atr_risk",
        "dividend_yield_absolute",
        "max_return",
        "turnover_level",
        "vol_of_vol",
    }
    assert all(row["family_id"] in row["cluster"] for row in representatives)
    assert len(payload["metrics"]["clusters"]) == 6


def test_rolling_windows_use_complete_consecutive_ic_samples() -> None:
    import polars as pl

    module = _module()
    dates = [module.date(2022, 1, day) for day in range(1, 9)]
    frame = pl.DataFrame({
        "date": dates,
        "n": [100] * len(dates),
        "ic": [0.01, 0.02, -0.01, 0.03, 0.04, -0.02, 0.05, 0.06],
    })
    windows = module.rolling_ic_windows(frame, window=3, step=2)
    assert [window.height for window in windows] == [3, 3, 3]
    assert [window["date"][0] for window in windows] == [dates[0], dates[2], dates[4]]
    assert [window["date"][-1] for window in windows] == [dates[2], dates[4], dates[6]]


def test_bootstrap_seed_is_stable_and_window_specific() -> None:
    module = _module()
    first = module.bootstrap_seed("full", 20, 5000)
    assert first == module.bootstrap_seed("full", 20, 5000)
    assert first != module.bootstrap_seed("recent", 20, 5000)
    assert first != module.bootstrap_seed("full", 40, 5000)


def test_failed_validation_is_rejected_even_if_most_gates_pass() -> None:
    module = _module()
    gates = {
        "frozen_core_chain_valid": True,
        "coverage_ge_0_90": True,
        "full_ic_ge_0_01": True,
        "validation_ic_positive": False,
        "recent_ic_positive": True,
        "yearly_positive_min_4_of_5": True,
        "period_bootstrap_ci_positive": True,
        "rolling_window_count_ge_24": True,
        "rolling_median_positive": True,
        "rolling_positive_ratio_ge_0_70": True,
        "rolling_worst_ic_ge_minus_0_01": True,
        "rolling_bootstrap_positive_ratio_ge_0_70": True,
        "rolling_worst_bootstrap_ci_low_ge_minus_0_01": True,
    }
    conclusion, reasons = module.core_conclusion(gates)
    assert conclusion == "rejected"
    assert reasons == ["validation_ic_positive"]


def test_insufficient_rolling_windows_is_inconclusive_when_core_is_directional() -> None:
    module = _module()
    gates = {
        "frozen_core_chain_valid": True,
        "coverage_ge_0_90": True,
        "full_ic_ge_0_01": True,
        "validation_ic_positive": True,
        "recent_ic_positive": True,
        "yearly_positive_min_4_of_5": True,
        "period_bootstrap_ci_positive": True,
        "rolling_window_count_ge_24": False,
        "rolling_median_positive": False,
        "rolling_positive_ratio_ge_0_70": False,
        "rolling_worst_ic_ge_minus_0_01": True,
        "rolling_bootstrap_positive_ratio_ge_0_70": False,
        "rolling_worst_bootstrap_ci_low_ge_minus_0_01": True,
    }
    conclusion, reasons = module.core_conclusion(gates)
    assert conclusion == "inconclusive"
    assert "rolling_window_count_ge_24" in reasons


def test_rolling_summary_allows_weak_minority_but_reports_worst() -> None:
    module = _module()
    records = [
        {"directional_ic": 0.03, "bootstrap": {"ci_low": 0.01}},
        {"directional_ic": 0.02, "bootstrap": {"ci_low": 0.005}},
        {"directional_ic": 0.01, "bootstrap": {"ci_low": -0.001}},
        {"directional_ic": -0.004, "bootstrap": {"ci_low": -0.009}},
        {"directional_ic": 0.04, "bootstrap": {"ci_low": 0.02}},
    ]
    summary = module.summarise_rolling_records(records)
    assert summary["window_count"] == 5
    assert summary["median_ic"] == 0.02
    assert summary["positive_ratio"] == 0.8
    assert summary["worst_ic"] == -0.004
    assert summary["bootstrap_positive_ratio"] == 0.6
    assert summary["worst_bootstrap_ci_low"] == -0.009


def test_committed_r532_result_is_contract_bound_and_keeps_negative_knowledge() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r532_factor_library_core_stability_preregistration.json"
    result = research / "r532_factor_library_core_stability_result.json"
    markdown = research / "r532_factor_library_core_stability_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "statistical_screen"
    assert payload["conclusion"] == "rejected"
    assert payload["preregistration_sha256"] == hashlib.sha256(
        prereg.read_bytes()
    ).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert payload["metrics"]["rolling_stability"]["summary"]["window_count"] == 40
    assert payload["gate_results"]["period_bootstrap_ci_positive"] is False
    assert payload["negative_knowledge"]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
