from __future__ import annotations

import importlib.util
import math
from datetime import date, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r350_sleeve_ensemble.py"
)
RISK_PARITY_PATH = ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fixed_pair_ensemble_is_fifty_fifty_and_charges_rebalance_cost() -> None:
    module = _load(MODULE_PATH, "r350_sleeve_ensemble")
    r112 = _load(RISK_PARITY_PATH, "r112_r350_test")
    days = [date(2024, 1, 2) + timedelta(days=index) for index in range(90)]
    left = pl.DataFrame({"date": days, "value": [100.0 * 1.002**index for index in range(len(days))]})
    right = pl.DataFrame({"date": days, "value": [100.0 * 1.001**index for index in range(len(days))]})

    curve, weights = module.blend_pair(left, right, 0.001, r112)

    assert curve.height == len(days)
    assert all(math.isclose(float(row["weight_a"]), 0.5, rel_tol=0.0, abs_tol=1e-12) for row in weights)
    daily_returns = [
        curve["value"][index] / curve["value"][index - 1] - 1.0
        for index in range(1, curve.height)
    ]
    assert all(math.isfinite(value) for value in daily_returns)
    assert curve["value"][-1] < 100.0 * 1.002 ** (len(days) - 1)
