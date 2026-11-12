from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
SUMMARY = EVIDENCE / "summarize_r368_capacity.py"
PRE_REG = EVIDENCE / "r368_capacity_preregistration.json"
R367_RESULT = EVIDENCE / "r367_size_neutralization_backtest.json"
PHASES = ["2022-05-05", "2023-01-03", "2024-01-02", "2025-01-02", "2025-10-14", "2026-01-05"]


def _load_module():
    spec = importlib.util.spec_from_file_location("r368_capacity_summary", SUMMARY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _portfolio(annual: float, sharpe: float, mdd: float, recent: float, phases: list[float], three_x: float):
    return {
        "2x": {"annual": annual, "sharpe": sharpe, "mdd": mdd},
        "3x": {"annual": three_x, "sharpe": sharpe - 0.1, "mdd": mdd - 0.01},
        "recent_2x": recent,
        "phase_2x": {phase: {"total_return": result} for phase, result in zip(PHASES, phases, strict=True)},
    }


def test_r368_failure_record_preserves_all_completed_curves() -> None:
    failure = json.loads((EVIDENCE / "r368_capacity_backtest_attempt1_failure.json").read_text(encoding="utf-8"))
    assert failure["backtest_cells_completed"] == 48
    assert failure["system_backtests_completed"] is True
    assert failure["curves_overwritten"] is False
    assert failure["summary_result_written"] is False


def test_r368_summary_reads_actual_r367_nested_metric_path() -> None:
    summary = _load_module()
    r367 = json.loads(R367_RESULT.read_text(encoding="utf-8"))

    raw_candidate = summary.r367_candidate_metrics(r367)

    assert raw_candidate == r367["metrics"]["size"]["candidate"]
    assert raw_candidate["2x"]["annual"] > 0


def test_r368_capacity_comparison_compares_both_arms_and_size_baseline() -> None:
    summary = _load_module()
    metrics = {
        "candidate": {
            "10": _portfolio(.30, 1.0, -.30, .01, [.1, .1, .1, .1, 0, 0], .25),
            "20": _portfolio(.31, .98, -.27, .05, [.1, .1, .1, .1, .01, 0], .26),
        },
        "control": {
            "10": _portfolio(.29, 1.01, -.25, .02, [.1, .1, .1, .1, 0, 0], .24),
            "20": _portfolio(.28, .97, -.24, .04, [.1, .1, .1, .1, .01, 0], .23),
        },
    }
    r367 = {"metrics": {"size": {"candidate": _portfolio(.30, 1.0, -.29, .04, [.1]*6, .25), "control": _portfolio(.29, 1.01, -.25, .02, [.1]*6, .24)}}}

    comparison = summary.compare_capacity_metrics(metrics, r367)

    assert abs(comparison["candidate_20_minus_10_annual"] - .01) < 1e-12
    assert abs(comparison["candidate_20_minus_10_mdd"] - .03) < 1e-12
    assert abs(comparison["candidate_20_minus_10_recent"] - .04) < 1e-12
    assert abs(comparison["control_20_minus_10_annual"] + .01) < 1e-12
    assert abs(comparison["candidate_control_edge_10"] - .01) < 1e-12
    assert abs(comparison["candidate_control_edge_20"] - .03) < 1e-12
    assert abs(comparison["candidate_control_edge_change_20_minus_10"] - .02) < 1e-12
    assert abs(comparison["cap20_candidate_annual_change_vs_r367"] - .01) < 1e-12


def test_r368_gate_evaluator_uses_preregistered_capacity_thresholds() -> None:
    summary = _load_module()
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    metrics = {
        "candidate": {
            "10": _portfolio(.30, 1.0, -.30, .01, [.1, .1, .1, .1, 0, 0], .25),
            "20": _portfolio(.31, .98, -.26, .05, [.1, .1, .1, .1, .01, 0], .26),
        },
        "control": {
            "10": _portfolio(.29, 1.01, -.25, .02, [.1, .1, .1, .1, 0, 0], .24),
            "20": _portfolio(.28, .97, -.24, .04, [.1, .1, .1, .1, .01, 0], .23),
        },
    }
    r367 = {"metrics": {"size": {"candidate": _portfolio(.30, 1.0, -.29, .04, [.1]*6, .25), "control": _portfolio(.29, 1.01, -.25, .02, [.1]*6, .24)}}}

    gates = summary.evaluate_capacity_gates(prereg, metrics, r367)

    assert list(gates) == list(prereg["performance_gates"])
    assert gates == {
        "G1_cap20_candidate_mdd_improves_3pp": True,
        "G2_cap20_candidate_recent_improves_3pp": True,
        "G3_cap20_candidate_annual_not_more_than_3pp_lower": True,
        "G4_cap20_sharpe_not_more_than_0_05_lower": True,
        "G5_candidate_control_edge_not_worse_2pp": True,
        "G6_cap20_candidate_recent_at_least_control": True,
        "G7_at_least_4_of_6_cap20_phases_positive": True,
        "G8_cap20_candidate_3x_annual_nonnegative": True,
        "G9_unlevered": True,
    }

def test_r368_reconciler_uses_original_frozen_preregistration_and_preflight() -> None:
    summary = _load_module()

    assert summary.PRE_REG == EVIDENCE / "r368_capacity_preregistration.json"
    assert summary.PRELIGHT == EVIDENCE / "r368_capacity_preflight.json"
    assert summary.FAILURE == EVIDENCE / "r368_capacity_backtest_attempt1_failure.json"
    assert summary.MANIFEST == EVIDENCE / "r368_reconciliation_manifest.json"


def test_r368_reconciler_expects_exactly_the_48_preserved_curves() -> None:
    summary = _load_module()
    expected = set(summary.expected_cache_names())
    actual = {path.name for path in summary.CACHE.glob("*.parquet")}

    assert len(expected) == 48
    assert actual == expected

def test_r368_reconciler_loads_preserved_parquet_curves() -> None:
    summary = _load_module()

    metrics, scorecard = summary.load_metrics_from_cache()

    assert set(metrics) == {"candidate", "control"}
    assert set(metrics["candidate"]) == {"10", "20"}
    assert len(scorecard["candidate"]["10"]) == 12
    assert len(scorecard["control"]["20"]) == 12
    assert metrics["candidate"]["20"]["2x"]["days"] == 1078
