from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from app.research.gate_policy_v2 import DEFAULT_POLICY_PATH, evaluate_gate_policy_v2

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
POLICY_PATH = EVIDENCE / "gate_policy_v2.json"
AUDIT_PATH = EVIDENCE / "audit_gate_policy_v2.py"
GATE_MODULE_PATH = ROOT / "backend/app/research/gate_policy_v2.py"
RECONCILIATION_PATH = EVIDENCE / "r369_gate_policy_reconciliation.json"


def _snapshot(
    *,
    annual: float = 0.11,
    sharpe: float = 1.20,
    mdd: float = -0.10,
    recent: float = 0.04,
    year_2022: float = -0.02,
    year_2023: float = 0.07,
) -> dict:
    return {
        "annual": annual,
        "sharpe": sharpe,
        "mdd": mdd,
        "total_return": 0.60,
        "recent": recent,
        "year_2022": year_2022,
        "year_2023": year_2023,
        "fresh_starts": [0.20, 0.15, 0.10, -0.01, 0.03, -0.02],
    }


def _candidate() -> dict:
    return {
        "id": "fixture",
        "max_positions": 10,
        "is_multi_asset": True,
        "data_integrity": {"pass": True, "checks": {"pit": True, "t_plus_one": True}},
        "no_leverage": True,
        "main_2x": _snapshot(),
        "control_2x": _snapshot(annual=0.12, sharpe=1.25, recent=0.05),
        "stress_3x": {
            "cost_factor": 3,
            "annual": 0.09,
            "sharpe": 1.10,
            "mdd": -0.11,
            "total_return": 0.50,
            "recent": 0.03,
        },
        "extreme_5x": {
            "cost_factor": 5,
            "annual": 0.07,
            "sharpe": 1.00,
            "mdd": -0.12,
            "total_return": 0.40,
            "recent": 0.02,
        },
        "concentration": {
            "distinct_symbols": 180,
            "top1_gross_share": 0.06,
            "top3_gross_share": 0.15,
            "top5_gross_share": 0.22,
            "top_industry_gross_share": 0.25,
            "max_symbol_loss_to_gross": 0.03,
            "asset_contribution_shares": {
                "equity": 0.40,
                "gold": 0.25,
                "treasury": 0.15,
                "dividend": 0.20,
            },
            "top1_net_share": 0.45,
            "top3_net_share": 1.10,
            "top5_net_share": 1.50,
            "net_to_gross": 0.12,
        },
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }


def test_default_policy_path_resolves_inside_repository() -> None:
    assert DEFAULT_POLICY_PATH.is_file()

    result = evaluate_gate_policy_v2(_candidate())

    assert result["policy_version"] == 2


def test_r369_reconciliation_binds_current_policy_gate_module_and_test() -> None:
    report = json.loads(RECONCILIATION_PATH.read_text(encoding="utf-8"))
    bindings = {
        "policy_sha256": POLICY_PATH,
        "gate_module_sha256": GATE_MODULE_PATH,
        "audit_script_sha256": AUDIT_PATH,
        "audit_test_sha256": Path(__file__).resolve(),
    }

    for key, path in bindings.items():
        assert report[key] == hashlib.sha256(path.read_bytes()).hexdigest(), key


def test_5x_negative_return_is_diagnostic_not_a_hard_rejection() -> None:
    candidate = _candidate()
    candidate["extreme_5x"] = {
        **candidate["extreme_5x"],
        "annual": -0.03,
        "sharpe": -0.10,
        "total_return": -0.10,
        "mdd": -0.35,
    }

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["hard_gates_pass"] is True
    assert "negative_5x_annual" in result["diagnostics"]["5x_warnings"]
    assert "5x_drawdown_deterioration" in result["diagnostics"]["5x_warnings"]


def test_net_concentration_is_reported_but_gross_concentration_is_the_hard_gate() -> None:
    candidate = _candidate()

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["gates"]["concentration"]["top5_gross_share"] is True
    assert result["gates"]["concentration"]["net_share_diagnostic_only"] is True
    assert result["diagnostics"]["net_concentration"]["top5_net_share"] == 1.50


def test_four_of_six_positive_fresh_starts_pass_when_median_is_positive() -> None:
    candidate = _candidate()
    candidate["main_2x"]["fresh_starts"] = [0.2, 0.1, -0.03, 0.04, -0.02, 0.01]

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["gates"]["core_2x"]["minimum_fresh_starts"] is True
    assert result["metrics"]["fresh_positive_count"] == 4
    assert result["metrics"]["fresh_median"] > 0


def test_data_integrity_failure_remains_hard_rejection() -> None:
    candidate = _candidate()
    candidate["data_integrity"] = {"pass": False, "checks": {"pit": False}}

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "rejected"
    assert result["hard_gates_pass"] is False
    assert result["gates"]["data_integrity"] is False


def test_missing_gross_shares_fall_back_to_passing_2x_net_gates_not_5x_net_shares() -> None:
    candidate = _candidate()
    candidate["concentration"] = {
        "distinct_symbols": 315,
        "top_industry_gross_share": 0.21,
        "max_symbol_loss_to_gross": 0.021,
        "legacy_2x_net_concentration_pass": True,
        "legacy_5x_net_concentration_pass": False,
        "top1_net_share": 0.29,
        "top3_net_share": 0.66,
        "top5_net_share": 0.92,
        "net_to_gross": 0.18,
        "asset_contribution_shares": {"equity": 0.40, "gold": 0.30, "debt": 0.30},
    }

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["gates"]["concentration"]["concentration_evidence"] is True
    assert result["diagnostics"]["concentration"]["gross_shares_available"] is False
    assert result["diagnostics"]["concentration"]["legacy_2x_net_fallback"] is True
    assert result["diagnostics"]["concentration"]["legacy_5x_net_not_used_as_hard_gate"] is True


def test_missing_asset_contribution_is_diagnostic_when_other_concentration_data_passes() -> None:
    candidate = _candidate()
    candidate["concentration"].pop("asset_contribution_shares")

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["gates"]["concentration"]["asset_contribution"] is True
    assert result["diagnostics"]["concentration"]["asset_contribution_available"] is False


def test_missing_control_recent_is_diagnostic_not_a_hidden_rejection() -> None:
    candidate = _candidate()
    candidate["control_2x"].pop("recent")

    result = evaluate_gate_policy_v2(candidate, policy_path=POLICY_PATH)

    assert result["status"] == "qualified_for_paper_review"
    assert result["gates"]["control_2x"]["recent_return"] is True
    assert result["diagnostics"]["control"]["recent_available"] is False


def test_reconciliation_recovers_candidates_misclassified_by_extreme_gates() -> None:
    spec = importlib.util.spec_from_file_location("audit_gate_policy_v2", AUDIT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.build_reconciliation()
    candidates = {item["id"]: item for item in report["candidates"]}

    assert report["post_hoc_gate_policy_correction"] is True
    assert candidates["r334_top10_three_asset_equity_cap33_posthoc"]["status"] == "qualified_for_paper_review"
    assert candidates["r344_extended_window_center"]["status"] == "qualified_for_paper_review"
    assert candidates["r350_dual_universe_ab_ensemble"]["status"] == "qualified_for_paper_review"
    assert candidates["r354_factor_agreement_gate_c_sleeve_cap10_gold_trend_proxy"]["status"] == "qualified_for_paper_review"
    assert candidates["r357_gold_commodity_dual_trend_alt_slot"]["status"] == "qualified_for_paper_review"
    assert candidates["r359_c_three_cohort_staggered"]["status"] == "qualified_for_paper_review"
    assert candidates["r360_industry_fade_quality_h20_sl20"]["status"] == "qualified_for_paper_review"
    assert candidates["r365_20d_weak_reversal"]["status"] == "rejected"
    assert candidates["r368_capacity_cap10"]["status"] == "rejected"
    assert report["summary"]["recovered_count"] >= 7


def test_policy_uses_realistic_2x_and_stress_thresholds() -> None:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    core = policy["tiers"]["core_2x"]["thresholds"]
    stress = policy["tiers"]["stress_3x"]["thresholds"]
    assert core["annual_return_min"] == 0.08
    assert core["sharpe_min"] == 1.0
    assert core["max_drawdown_min"] == -0.20
    assert core["year_2022_min"] == -0.05
    assert core["fresh_starts_positive_min"] == 4
    assert stress["annual_return_min"] == 0.05
    assert stress["sharpe_min"] == 0.80
    assert stress["max_drawdown_min"] == -0.25
    assert policy["tiers"]["diagnostic_5x"]["hard_rejection"] is False
    assert policy["cost_semantics"]["5x"] == "transaction-cost stress, not leverage"
