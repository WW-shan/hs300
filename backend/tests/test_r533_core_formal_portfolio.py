from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r533_core_formal_portfolio.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "r533_core_formal_portfolio_test", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_project_formal_contract_and_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r533a_core_formal_portfolio_preregistration.json" in text
    assert "r532_factor_library_core_stability_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_training_grid_has_nine_fixed_execution_candidates() -> None:
    module = _module()
    rows = module.training_candidates()
    assert len(rows) == 9
    assert {(row["max_positions"], row["max_hold_days"]) for row in rows} == {
        (10, 20),
        (10, 30),
        (10, 60),
        (20, 20),
        (20, 30),
        (20, 60),
        (30, 20),
        (30, 30),
        (30, 60),
    }
    assert all(row["entry_score"] == 70 for row in rows)
    assert all(row["exit_score"] == 40 for row in rows)


def test_training_selection_is_deterministic_and_uses_bootstrap_first() -> None:
    module = _module()
    rows = [
        {
            "max_positions": 10,
            "max_hold_days": 20,
            "stats": {"annual_return": 0.12, "sharpe": 1.4, "max_drawdown": -0.18},
            "bootstrap": {"total_p05": 0.01},
            "trade_count": 30,
        },
        {
            "max_positions": 20,
            "max_hold_days": 30,
            "stats": {"annual_return": 0.10, "sharpe": 1.1, "max_drawdown": -0.12},
            "bootstrap": {"total_p05": 0.04},
            "trade_count": 40,
        },
        {
            "max_positions": 30,
            "max_hold_days": 60,
            "stats": {"annual_return": -0.01, "sharpe": 0.2, "max_drawdown": -0.35},
            "bootstrap": {"total_p05": -0.10},
            "trade_count": 10,
        },
    ]
    selected = module.select_training_candidate(rows)
    assert selected is not None
    assert selected["max_positions"] == 20
    assert selected["max_hold_days"] == 30


def test_training_selection_rejects_all_when_no_candidate_passes() -> None:
    module = _module()
    rows = [
        {
            "max_positions": 20,
            "max_hold_days": 30,
            "stats": {"annual_return": 0.02, "sharpe": 1.2, "max_drawdown": -0.10},
            "bootstrap": {"total_p05": 0.01},
            "trade_count": 50,
        }
    ]
    assert module.select_training_candidate(rows) is None


def test_concentration_metrics_use_gross_profit() -> None:
    module = _module()
    trades = [
        {"symbol": "A", "pnl_amount": 60.0},
        {"symbol": "B", "pnl_amount": 20.0},
        {"symbol": "C", "pnl_amount": 10.0},
        {"symbol": "D", "pnl_amount": 10.0},
        {"symbol": "A", "pnl_amount": -10.0},
    ]
    industries = {"A": "tech", "B": "tech", "C": "bank", "D": "energy"}
    result = module.concentration_metrics(
        trades,
        industries,
        max_positions=10,
    )
    assert result["distinct_symbols"] == 4
    assert result["top1_gross_share"] == 0.6
    assert result["top3_gross_share"] == 0.9
    assert result["top5_gross_share"] == 1.0
    assert result["top_industry_gross_share"] == 0.8
    assert result["max_symbol_loss_to_gross"] == 0.1
    assert result["minimum_distinct_symbols"] == 40


def test_scaled_core_score_is_bounded_zero_to_one_hundred() -> None:
    import polars as pl

    module = _module()
    core = pl.DataFrame({
        "symbol": ["A", "B", "C"],
        "date": [module.date(2024, 1, 2)] * 3,
        "factor_value": [0.0, 0.5, 1.0],
        "n_factors": [6, 6, 6],
    })
    scored = module.scaled_score_panel(core)
    assert scored["score"].to_list() == [0.0, 50.0, 100.0]


def test_frozen_inputs_are_exactly_the_six_r532_representatives() -> None:
    module = _module()
    inputs = module.load_frozen_inputs(ROOT)
    assert {row["family_id"] for row in inputs["representatives"]} == module.CORE_FACTORS
    assert len(inputs["representatives"]) == 6
    assert inputs["r532_payload"]["research_type"] == "statistical_screen"


def test_yearly_returns_use_equity_curve_dates() -> None:
    module = _module()
    curve = [
        {"date": "2023-12-29", "value": 100.0},
        {"date": "2024-01-02", "value": 110.0},
        {"date": "2024-12-31", "value": 121.0},
        {"date": "2025-01-02", "value": 120.0},
    ]
    assert module.yearly_returns(curve) == {
        "2024": 0.21,
        "2025": -0.008264,
    }


def test_config_symbols_follow_prepared_requested_universe_not_filtered_market_axes() -> None:
    module = _module()
    class Prepared:
        signature = ("strategy", ("A", "B", "C"),)
    assert module.config_symbols_for_prepared(Prepared()) == ["A", "B", "C"]


def test_committed_r533a_result_is_formal_and_preserves_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r533a_core_formal_portfolio_preregistration.json"
    result = research / "r533a_core_formal_portfolio_result.json"
    markdown = research / "r533a_core_formal_portfolio_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "project_formal"
    assert payload["conclusion"] == "rejected"
    assert payload["formal_evidence"]["persistence"] == "saved"
    assert payload["formal_evidence"]["lookahead"] == "passed"
    assert payload["formal_evidence"]["execution_timing"] == "passed"
    assert payload["preregistration_sha256"] == hashlib.sha256(
        prereg.read_bytes()
    ).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert payload["metrics"]["selected"]["max_positions"] == 10
    assert payload["metrics"]["selected"]["max_hold_days"] == 60
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
