from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r549_quality_addon_portfolio.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r549_quality_addon_portfolio", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r549_declares_project_formal_contract() -> None:
    module = _runner()
    assert module.RESEARCH_ID == "R549B"
    assert module.RESEARCH_TYPE == "project_formal"
    assert module.RESEARCH_PREREGISTRATION.endswith(
        "r549b_quality_addon_portfolio_preregistration.json"
    )
    assert module.VARIANTS["control"]["addons"] == ()
    assert module.VARIANTS["cash_quality"]["addons"] == ("cf_r547_cash_quality",)
    assert module.VARIANTS["safety"]["addons"] == (
        "cf_r547_value_quality_safety",
    )
    assert module.VARIANTS["cash_safety"]["addons"] == (
        "cf_r547_cash_quality",
        "cf_r547_value_quality_safety",
    )


def test_r549_selection_uses_train_and_validation_not_recent() -> None:
    module = _runner()
    selected = module.select_variant(
        {
            "control": {
                "train": {"annual_return": 0.10, "sharpe": 1.0, "max_drawdown": -0.12, "n_trades": 30},
                "validation": {"annual_return": 0.05, "sharpe": 1.0, "max_drawdown": -0.08, "n_trades": 20},
                "recent": {"annual_return": 0.20},
            },
            "cash_quality": {
                "train": {"annual_return": 0.11, "sharpe": 1.0, "max_drawdown": -0.12, "n_trades": 30},
                "validation": {"annual_return": 0.08, "sharpe": 1.0, "max_drawdown": -0.08, "n_trades": 20},
                "recent": {"annual_return": -0.20},
            },
            "safety": {
                "train": {"annual_return": 0.10, "sharpe": 1.0, "max_drawdown": -0.12, "n_trades": 30},
                "validation": {"annual_return": 0.07, "sharpe": 1.0, "max_drawdown": -0.08, "n_trades": 20},
                "recent": {"annual_return": 0.50},
            },
        }
    )
    assert selected == "cash_quality"


def test_r549_selection_rejects_negative_validation_even_with_strong_recent() -> None:
    module = _runner()
    selected = module.select_variant(
        {
            "cash_quality": {
                "train": {"annual_return": 0.15, "sharpe": 1.2, "max_drawdown": -0.10, "n_trades": 30},
                "validation": {"annual_return": -0.01, "sharpe": 1.0, "max_drawdown": -0.08, "n_trades": 20},
                "recent": {"annual_return": 0.50},
            }
        }
    )
    assert selected is None


def test_r549_final_gate_requires_absolute_return_recent_positive_and_fresh_starts() -> None:
    module = _runner()
    gate = module.final_gate(
        full={"annual_return": 0.11, "sharpe": 1.0, "max_drawdown": -0.15},
        recent={"total_return": 0.01},
        validation={"annual_return": 0.02},
        fresh_values=[0.1, 0.02, -0.01, 0.03, 0.04, 0.05],
        stress_3x={"annual_return": 0.09, "max_drawdown": -0.18, "recent_total_return": -0.01},
        warning_5x={"annual_return": 0.05, "max_drawdown": -0.19},
    )
    assert gate["core_pass"] is True
    assert gate["fresh_start"]["pass"] is True
    assert gate["conclusion"] == "watch_only"

    rejected = module.final_gate(
        full={"annual_return": 0.09, "sharpe": 1.0, "max_drawdown": -0.15},
        recent={"total_return": 0.01},
        validation={"annual_return": 0.02},
        fresh_values=[0.1, 0.02, -0.01, 0.03, 0.04, 0.05],
        stress_3x={"annual_return": 0.09, "max_drawdown": -0.18, "recent_total_return": -0.01},
        warning_5x={"annual_return": 0.05, "max_drawdown": -0.19},
    )
    assert rejected["conclusion"] == "rejected"
    assert "full_annual_min" in rejected["failed_gates"]
