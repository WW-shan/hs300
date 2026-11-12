from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
AUDIT = EVIDENCE / "reconcile_r365_preregistered_gates.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("r365_gate_reconciliation", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metrics(*, annual: float, sharpe: float, mdd: float, recent: float, phases: list[float], three_x: float) -> dict:
    return {
        "2x": {"annual": annual, "sharpe": sharpe, "mdd": mdd},
        "3x": {"annual": three_x},
        "recent_2x": recent,
        "phase_2x": {str(i): {"total_return": value} for i, value in enumerate(phases)},
    }


def test_gate_reconciliation_uses_preregistered_horizon_thresholds() -> None:
    module = _load_module()
    prereg = {
        "performance_gates": {
            "G1_candidate_recent_return_improves_at_least_5pp_vs_5d": "frozen",
            "G2_20d_candidate_mdd_within_3pp_of_5d": "frozen",
            "G3_20d_marginal_annual_edge_not_more_than_1pp_worse": "frozen",
            "G4_candidate_sharpe_within_0_10_of_5d": "frozen",
            "G5_candidate_annual_at_least_control_minus_1pp": "frozen",
            "G6_candidate_recent_not_worse_than_control": "frozen",
            "G7_at_least_3_of_6_phase_starts_positive": "frozen",
            "G8_candidate_3x_cost_annual_nonnegative": "frozen",
            "G9_unlevered": "frozen",
        },
        "rule": {"max_exposure_pct": 1.0},
        "decision": {"leverage": "none"},
    }
    r363 = {
        "metrics": {
            "candidate": _metrics(annual=0.12, sharpe=0.58, mdd=-0.23, recent=-0.07, phases=[0.1] * 6, three_x=0.07),
            "control": _metrics(annual=0.08, sharpe=0.45, mdd=-0.26, recent=-0.11, phases=[0.1] * 6, three_x=0.03),
        }
    }
    r365 = {
        "metrics": {
            "candidate": _metrics(annual=0.30, sharpe=0.99, mdd=-0.31, recent=0.08, phases=[0.1, 0.1, 0.1, -0.1, 0.0, 0.0], three_x=0.27),
            "control": _metrics(annual=0.38, sharpe=1.19, mdd=-0.23, recent=0.15, phases=[0.1] * 6, three_x=0.35),
        }
    }
    candidate_edge_20d = r365["metrics"]["candidate"]["2x"]["annual"] - r365["metrics"]["control"]["2x"]["annual"]
    candidate_edge_5d = r363["metrics"]["candidate"]["2x"]["annual"] - r363["metrics"]["control"]["2x"]["annual"]
    r365["comparison_vs_r363_five_day_matched_candidate_and_control"] = {
        "candidate_minus_control_annual_effect_20d": candidate_edge_20d,
        "candidate_minus_control_annual_effect_5d": candidate_edge_5d,
    }

    gates = module.evaluate_preregistered_gates(prereg, r365, r363)

    assert list(gates) == list(prereg["performance_gates"])
    assert gates == {
        "G1_candidate_recent_return_improves_at_least_5pp_vs_5d": True,
        "G2_20d_candidate_mdd_within_3pp_of_5d": False,
        "G3_20d_marginal_annual_edge_not_more_than_1pp_worse": False,
        "G4_candidate_sharpe_within_0_10_of_5d": True,
        "G5_candidate_annual_at_least_control_minus_1pp": False,
        "G6_candidate_recent_not_worse_than_control": False,
        "G7_at_least_3_of_6_phase_starts_positive": True,
        "G8_candidate_3x_cost_annual_nonnegative": True,
        "G9_unlevered": True,
    }


def test_r365_gate_audit_source_matches_frozen_preregistration() -> None:
    prereg = json.loads((EVIDENCE / "r365_weak_reversal_20d_preregistration.json").read_text(encoding="utf-8"))
    audit = _load_module()
    current = json.loads((EVIDENCE / "r365_weak_reversal_20d_backtest.json").read_text(encoding="utf-8"))
    prior = json.loads((EVIDENCE / "r363_weak_reversal_blend_backtest.json").read_text(encoding="utf-8"))

    gates = audit.evaluate_preregistered_gates(prereg, current, prior)

    assert list(gates) == list(prereg["performance_gates"])
    assert gates["G1_candidate_recent_return_improves_at_least_5pp_vs_5d"] is True
    assert gates["G2_20d_candidate_mdd_within_3pp_of_5d"] is False
    assert gates["G3_20d_marginal_annual_edge_not_more_than_1pp_worse"] is False
    assert gates["G4_candidate_sharpe_within_0_10_of_5d"] is True
    assert gates["G7_at_least_3_of_6_phase_starts_positive"] is True
