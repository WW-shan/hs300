from __future__ import annotations

import importlib.util
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_r548_multi_horizon_incremental_screen.py"


def _runner():
    spec = importlib.util.spec_from_file_location("r548_multi_horizon_incremental_screen", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r548_declares_statistical_screen_contract() -> None:
    module = _runner()
    assert module.RESEARCH_TYPE == "statistical_screen"
    assert module.RESEARCH_ID == "R548"
    assert module.HORIZONS == (20, 60)
    assert module.RESEARCH_PREREGISTRATION.endswith(
        "r548_multi_horizon_incremental_screen_preregistration.json"
    )


def test_r548_adds_future_returns_without_changing_signal_rows() -> None:
    module = _runner()
    frame = pl.DataFrame(
        {
            "symbol": ["a"] * 6,
            "date": pl.date_range(
                start=pl.date(2024, 1, 1), end=pl.date(2024, 1, 6), interval="1d", eager=True
            ),
            "close": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        }
    )
    result = module.add_horizon_returns(frame, horizons=(2, 3))
    assert result.height == frame.height
    assert result["forward_return_2d"][0] == pytest.approx(0.2)
    assert result["forward_return_3d"][0] == pytest.approx(0.3)
    assert result["forward_return_2d"][-1] is None


def test_r548_gate_accepts_one_passing_horizon_and_ignores_recent_for_selection() -> None:
    module = _runner()
    passing_60 = {
        "coverage": {"factor_non_null_ratio": 0.9, "valid_dates": 800},
        "train_ic": 0.02,
        "validation_ic": 0.01,
        "recent_ic": -0.02,
        "incremental": {
            "train": {"directional_delta": 0.004, "nw_p": 0.10, "p_value": 0.10}
        },
        "q_value": 0.12,
        "correlation_to_stable7": 0.4,
    }
    candidate = module.candidate_gate(
        {
            "horizons": {
                "20": {
                    **passing_60,
                    "train_ic": -0.01,
                    "validation_ic": -0.01,
                },
                "60": passing_60,
            },
            "recent_ic": -0.02,
        }
    )
    assert candidate["horizon_checks"]["20"]["pass"] is False
    assert candidate["horizon_checks"]["60"]["pass"] is True
    assert candidate["selection_pass"] is True
    assert candidate["primary_horizon"] == 60
    assert candidate["conclusion"] == "finalist"

    rejected = module.candidate_gate(
        {
            "horizons": {
                "20": {**passing_60, "validation_ic": -0.1},
                "60": {**passing_60, "train_ic": -0.1},
            },
            "recent_ic": 0.2,
        }
    )
    assert rejected["selection_pass"] is False
    assert rejected["conclusion"] == "rejected"
