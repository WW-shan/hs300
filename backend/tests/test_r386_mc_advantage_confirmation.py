from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r386_mc_advantage_confirmation.py"
PREREG = EVIDENCE / "r386_preregistration.json"
REPORT = EVIDENCE / "r386_mc_advantage_confirmation.json"
RESULT = EVIDENCE / "r386_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r386_mc_advantage_confirmation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fold(annual: float, sharpe: float, mdd: float = -0.05) -> dict[str, float]:
    return {
        "total_return": annual,
        "annual": annual,
        "sharpe": sharpe,
        "mdd": mdd,
        "days": 253,
    }


def _candidate(sample: int, annual: float, sharpe: float, cap: float) -> dict:
    return {
        "sample": sample,
        "raw": {
            "c_satellite_cap": cap,
            "equity_target_vol": 0.17,
            "overlay_lookback_days": 110,
            "overlay_target_vol": 0.17,
            "overlay_equity_weight_max": 0.37,
        },
        "parameters": {
            "c_satellite_cap": cap,
            "equity_target_vol": 0.17,
            "overlay_lookback_days": 110,
            "overlay_target_vol": 0.17,
            "overlay_equity_weight_max": 0.37,
        },
        "training_folds": [
            _fold(annual, sharpe),
            _fold(annual, sharpe),
            _fold(annual, sharpe),
        ],
    }


def test_preregistration_preserves_disclosure_thresholds_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R386"
    assert payload["disclosure"]["scratch_scan_performed"] is True
    assert payload["disclosure"]["status"].startswith("R386 is a disclosed confirmation")
    assert (
        payload["source_evidence"][
            "all_sidecars_and_input_hashes_must_match_before_performance_is_read"
        ]
        is True
    )
    assert payload["monte_carlo_parameter_synthesis"]["eligibility"] == {
        "every_training_fold_return_positive": True,
        "every_training_fold_sharpe_min": 1.2,
        "every_training_fold_mdd_min": -0.1,
    }
    assert payload["candidate_gates"]["local_monte_carlo"]["samples"] == 128
    assert payload["candidate_gates"]["local_monte_carlo"]["unique_samples_min"] == 120
    assert payload["candidate_gates"]["local_monte_carlo"]["pass_rate_2x_min"] == 0.95
    assert payload["candidate_gates"]["local_monte_carlo"]["pass_rate_3x_min"] == 0.95
    assert payload["candidate_gates"]["axis_neighbour_plateau"]["require_all_eleven"] is True
    assert payload["candidate_gates"]["five_x"]["role"].startswith("diagnostic warning only")
    assert payload["candidate_gates"]["no_leverage"]["max_weight_sum"] == 1.0


def test_practical_rounding_is_deterministic_and_uses_registered_increments() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    raw = {
        "c_satellite_cap": 0.1349,
        "equity_target_vol": 0.1749,
        "overlay_lookback_days": 112.6,
        "overlay_target_vol": 0.1649,
        "overlay_equity_weight_max": 0.3651,
    }

    rounded = module.practical_round_parameters(raw, prereg)

    assert rounded == {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 115,
        "overlay_target_vol": 0.16,
        "overlay_equity_weight_max": 0.37,
    }


def test_advantage_selection_uses_training_folds_only() -> None:
    module = _module()
    rows = [
        _candidate(0, 0.11, 1.40, 0.10),
        _candidate(1, 0.13, 1.30, 0.11),
        _candidate(2, 0.12, 1.50, 0.09),
    ]
    rows[0]["validation"] = {"total_return": 99.0, "sharpe": 99.0}
    rows[1]["full"] = {"annual": -1.0, "sharpe": -1.0}

    selected = module.select_advantage_candidate(rows)
    rows[0]["validation"]["total_return"] = -99.0
    selected_after_poisoning_holdout = module.select_advantage_candidate(rows)

    assert selected["sample"] == 1
    assert selected_after_poisoning_holdout["sample"] == 1
    assert selected["selection"]["eligible_count"] == 3


def test_defensive_selection_maximizes_worst_training_fold() -> None:
    module = _module()
    rows = [
        _candidate(0, 0.14, 1.2, 0.10),
        _candidate(1, 0.12, 1.5, 0.11),
        _candidate(2, 0.13, 1.3, 0.12),
    ]
    rows[0]["training_folds"][2] = _fold(0.08, 1.2)
    rows[1]["training_folds"][2] = _fold(0.11, 1.5)
    rows[2]["training_folds"][2] = _fold(0.10, 1.3)

    selected = module.select_defensive_candidate(rows)

    assert selected["sample"] == 1


def test_five_x_is_warning_only_even_with_poor_metrics() -> None:
    module = _module()

    result = module.evaluate_five_x({"annual": 0.01, "sharpe": 0.2, "mdd": -0.40})

    assert result["warning"] is True
    assert result["hard_rejection"] is False


def test_axis_neighbourhood_contains_centre_and_two_sides_per_dimension() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    centre = {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 110,
        "overlay_target_vol": 0.17,
        "overlay_equity_weight_max": 0.37,
    }

    rows = module.axis_neighbourhood(centre, prereg)

    assert len(rows) == 11
    assert rows[0]["label"] == "centre"
    assert rows[0]["parameters"] == centre
    assert len({tuple(row["parameters"].values()) for row in rows}) == 11
    assert {row["dimension"] for row in rows[1:]} == set(centre)
    assert {row["offset_steps"] for row in rows[1:]} == {-1, 1}


def test_local_monte_carlo_deduplicates_after_practical_rounding() -> None:
    module = _module()
    samples = [
        {"sample": 0, "parameters": {"overlay_lookback_days": 110.0}},
        {"sample": 1, "parameters": {"overlay_lookback_days": 110.0}},
        {"sample": 2, "parameters": {"overlay_lookback_days": 115.0}},
    ]

    unique = module.deduplicate_parameter_samples(samples)

    assert [row["sample"] for row in unique] == [0, 2]


def test_training_fold_uses_exactly_252_returns() -> None:
    pl = pytest.importorskip("polars")
    module = _module()
    dates = pl.date_range(
        pl.date(2024, 1, 1),
        pl.date(2025, 12, 31),
        interval="1d",
        eager=True,
    )[:300]
    values = [1.0 + index * 0.001 for index in range(len(dates))]
    curve = pl.DataFrame({"date": dates, "value": values})

    fold = module.training_fold_metrics(curve, module.date(2024, 1, 1))

    assert fold["days"] == 253
    assert fold["start"] == "2024-01-01"
    assert fold["end"] == str(dates[252])
    assert fold["total_return"] == pytest.approx(values[252] / values[0] - 1.0)


def test_regime_inputs_are_shifted_before_rules_see_them() -> None:
    pl = pytest.importorskip("polars")
    module = _module()
    frame = pl.DataFrame(
        {
            "date": pl.date_range(pl.date(2024, 1, 1), pl.date(2024, 1, 5), eager=True),
            "state": ["weak", "strong", "weak", "range", "strong"],
            "score": [10, 20, 30, 40, 50],
            "trend_score": [11, 21, 31, 41, 51],
            "above_ma20_pct": [0.1, 0.2, 0.3, 0.4, 0.5],
            "index_pct": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
    )

    signals = module.lagged_regime_signals(frame)

    assert signals["state_lag1"][0] is None
    assert signals["state_lag1"][1] == "weak"
    assert signals["trend_score_lag1"][4] == 41
    assert signals["above_ma20_pct_lag1"][3] == pytest.approx(0.3)


def test_r386_evidence_matches_actual_backtest_contract_and_sidecars() -> None:
    for path in (PREREG, RUNNER, REPORT, RESULT):
        if path == RUNNER:
            continue
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.exists(), path
        expected = sidecar.read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected

    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["round"] == "R386"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["all_pass"] is True
    assert report["no_leverage"] is True
    assert report["decision"]["status"] == "candidate_for_paper_trading_review"
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["production_enabled"] is False

    selection = report["selection"]
    assert selection["advantage"]["sample"] == 14
    assert selection["advantage"]["parameters"] == {
        "c_satellite_cap": 0.13,
        "equity_target_vol": 0.17,
        "overlay_lookback_days": 110,
        "overlay_target_vol": 0.17,
        "overlay_equity_weight_max": 0.37,
    }
    assert selection["defensive"]["sample"] == 62

    candidate = report["candidate"]
    assert candidate["checks"]["full_2x"] is True
    assert candidate["checks"]["full_3x"] is True
    assert candidate["checks"]["holdout"] is True
    assert candidate["checks"]["increment_over_r385_centre"] is True
    assert candidate["checks"]["paired_bootstrap"] is True
    assert candidate["metrics"]["2x"]["full"]["annual"] > 0.14
    assert candidate["metrics"]["2x"]["full"]["sharpe"] > 1.45
    assert candidate["metrics"]["2x"]["full"]["mdd"] >= -0.10
    assert candidate["metrics"]["2x"]["sealed"]["total_return"] > 0.25
    assert candidate["increments_over_r385_centre"]["full_annual"] > 0.0025

    axis = report["axis_neighbourhood"]
    assert axis["row_count"] == axis["pass_count_2x"] == axis["pass_count_3x"] == 11
    assert axis["all_pass"] is True

    local = report["local_monte_carlo"]
    assert local["requested_samples"] == 128
    assert local["unique_sample_count"] >= 120
    assert local["pass_rate_2x"] >= 0.95
    assert local["pass_rate_3x"] >= 0.95

    regime = report["regime_conditioning"]
    assert regime["advanced_rules"] == []
    assert regime["selected_rule"] is None
    assert regime["accepted"] is False
    assert regime["negative_knowledge"] is True

    five = report["five_x_diagnostic"]
    assert five["hard_rejection"] is False
    assert five["max_weight_sum"] <= 1.0 + 1e-12

    stable = report["stable_platform"]
    assert stable["r385_cells_pass_both_costs"] == 27
    assert stable["r385_platform_cells_pass_both_costs"] == 7
    assert stable["local_axis_neighbours_pass_both_costs"] == 11
    assert stable["local_monte_carlo_unique_pass_rate_min"] >= 0.95
    assert stable["dynamic_platform_selection_supported"] is False
    assert "parameter robustness" in stable["claim_scope"]

    exposures = [candidate["exposures"][cost]["max_weight_sum"] for cost in ("2x", "3x", "5x")]
    exposures.extend(row["pass"]["2x"]["max_weight_sum"] for row in axis["rows"])
    exposures.extend(row["pass"]["2x"]["max_weight_sum"] for row in local["rows"])
    assert all(value <= 1.0 + 1e-12 for value in exposures)
    assert all(
        "full" not in row and "sealed" not in row and "recent" not in row
        for row in report["selection"]["training_rows"]
    )
    result_text = RESULT.read_text(encoding="utf-8")
    assert "27/27" in result_text
    assert "7/7" in result_text


def test_r386_report_does_not_generate_execution_artifacts() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    serialized = json.dumps(report, ensure_ascii=False).lower()

    assert "paper_target" not in report["decision"]
    assert report["decision"]["paper_target_generated"] is False
    assert "production_enabled" in serialized
    assert not (EVIDENCE / "r386_orders.json").exists()
    assert not (EVIDENCE / "r386_paper_targets.json").exists()
    assert not (EVIDENCE / "r386_production.json").exists()
