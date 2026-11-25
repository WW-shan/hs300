from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _runner():
    import importlib.util
    import sys

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/research_r545_stable7_risk_budget.py"
    spec = importlib.util.spec_from_file_location("research_r545_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ctx(*, drawdown: float = 0.0, returns: list[float] | None = None):
    returns = returns or [0.01, -0.01, 0.005, -0.005]
    selected = tuple(SimpleNamespace(symbol=f"S{i}", score=10 - i) for i in range(2))
    return SimpleNamespace(
        selected=selected,
        recent_returns={f"S{i}": returns for i in range(2)},
        closed_trades=(),
        portfolio=SimpleNamespace(
            cash=100_000.0,
            equity=100_000.0,
            drawdown=drawdown,
            open_positions=0,
        ),
        params={},
    )


def test_fixed_cash_policy_scales_only_budget() -> None:
    runner = _runner()
    spec = next(row for row in runner.risk_variants() if row["id"] == "cash_60")
    result = runner.make_policy(spec)(_ctx())
    assert result.weights == pytest.approx([0.5, 0.5])
    assert result.budget_scale == pytest.approx(0.6)


def test_drawdown_guard_uses_current_drawdown_boundary() -> None:
    runner = _runner()
    spec = next(row for row in runner.risk_variants() if row["id"] == "dd_10")
    compute = runner.make_policy(spec)
    assert compute(_ctx(drawdown=-0.099)).budget_scale == pytest.approx(1.0)
    assert compute(_ctx(drawdown=-0.10)).budget_scale == pytest.approx(0.5)


def test_vol_target_only_shrinks_and_uses_recent_returns() -> None:
    runner = _runner()
    spec = next(row for row in runner.risk_variants() if row["id"] == "vol_10")
    compute = runner.make_policy(spec)
    low_vol = compute(_ctx(returns=[0.001, -0.001, 0.001, -0.001]))
    high_vol = compute(_ctx(returns=[0.05, -0.05, 0.05, -0.05]))
    assert low_vol.budget_scale <= 1.0
    assert high_vol.budget_scale >= 0.5
    assert high_vol.budget_scale <= low_vol.budget_scale


def test_combined_budget_takes_minimum_scale() -> None:
    runner = _runner()
    spec = next(row for row in runner.risk_variants() if row["id"] == "vol_12_dd_10")
    result = runner.make_policy(spec)(_ctx(
        drawdown=-0.20,
        returns=[0.05, -0.05, 0.05, -0.05],
    ))
    assert result.budget_scale == pytest.approx(0.5)


def test_training_selection_prefers_better_mdd_after_eligibility() -> None:
    runner = _runner()
    rows = [
        {
            "variant": {"id": "a", "order": 0},
            "stats": {"annual_return": 0.15, "sharpe": 1.1, "max_drawdown": -0.18},
            "trade_count": 30,
        },
        {
            "variant": {"id": "b", "order": 1},
            "stats": {"annual_return": 0.14, "sharpe": 1.0, "max_drawdown": -0.12},
            "trade_count": 30,
        },
        {
            "variant": {"id": "c", "order": 2},
            "stats": {"annual_return": 0.20, "sharpe": 1.5, "max_drawdown": -0.25},
            "trade_count": 30,
        },
    ]
    assert runner.select_training_variant(rows)["variant"]["id"] == "b"


def test_scientific_design_freezes_22_formal_runs() -> None:
    runner = _runner()
    design = runner.scientific_design()
    assert tuple(design["members"]) == runner.EXPECTED_MEMBERS
    assert design["variants"] == 9
    assert design["confirmation_runs"] == 13
    assert design["formal_runs"] == 22
    assert design["bootstrap_runs"] == 5000
    assert design["recent_is_independent_oos"] is False
    assert design["execution"]["leverage"] == "none"
    assert design["execution"]["max_exposure_pct"] == 1.0
    assert design["output_policy"]["active_version"] is None
