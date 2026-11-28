from __future__ import annotations

from pathlib import Path

import pytest

from app.factor_research.standard_parameter_mc import (
    bootstrap_equity_curve,
    build_standard_parameter_artifacts,
    select_standard_parameter,
)
from app.factor_research.standard_validation import parameter_bootstrap_series


def _point(
    parameter: int,
    *,
    train: float = 0.10,
    validation: float = 0.02,
    recent: float = 0.50,
    mdd: float = -0.12,
    plateau: bool = True,
    cost_monotonic: bool = True,
    trades: int = 30,
) -> dict:
    return {
        "parameter": parameter,
        "params": {"positions": parameter},
        "train": {
            "annual_return": train,
            "sharpe": 1.0,
            "max_drawdown": mdd,
            "n_trades": trades,
            "coverage": 0.95,
        },
        "validation": {
            "annual_return": validation,
            "sharpe": 0.8,
            "max_drawdown": mdd / 2,
            "n_trades": max(20, trades // 2),
            "coverage": 0.90,
        },
        "recent": {"annual_return": recent, "sharpe": 2.0},
        "plateau_ok": plateau,
        "cost_monotonic": cost_monotonic,
    }


def test_standard_parameter_selection_ignores_recent_results() -> None:
    result = select_standard_parameter(
        [
            _point(8, train=0.11, validation=0.02, recent=-0.50),
            _point(10, train=0.11, validation=0.02, recent=0.50),
        ],
        {"center": {"positions": 8}},
    )
    assert result["selected"]["parameter"] == 8
    assert result["recent_used_for_selection"] is False
    assert result["selection_policy"] == "train_select_validation_confirm"


def test_standard_parameter_selection_does_not_fabricate_bootstrap_distribution() -> None:
    result = select_standard_parameter([_point(8), _point(10)], {})

    assert result["bootstrap"] == {}


def test_standard_parameter_selection_uses_supplied_bootstrap_distribution() -> None:
    bootstrap = {
        "method": "moving_block_bootstrap",
        "seed": 1,
        "windows": {"train": {"total_p05": 0.01, "total_p50": 0.12, "total_p95": 0.30}},
    }
    result = select_standard_parameter(
        [_point(8), _point(10, validation=0.03)],
        {},
        bootstrap=bootstrap,
    )

    assert result["selected"]["parameter"] == 10
    assert result["bootstrap"] == bootstrap


def test_bootstrap_equity_curve_is_deterministic_and_reports_path_distribution() -> None:
    curve = [
        {"date": f"2024-01-{index + 1:02d}", "value": 1.0 + index * 0.001}
        for index in range(253)
    ]

    first = bootstrap_equity_curve(curve, block_size=20, bootstrap_paths=200, seed=7)
    second = bootstrap_equity_curve(curve, block_size=20, bootstrap_paths=200, seed=7)

    assert first == second
    assert first["method"] == "moving_block_bootstrap"
    assert first["block_size"] == 20
    assert first["bootstrap_paths"] == 200
    assert first["seed"] == 7
    assert first["observations"] == 252
    assert first["total_p05"] <= first["total_p50"] <= first["total_p95"]
    assert first["prob_positive"] == 1.0


def test_parameter_bootstrap_series_uses_real_window_distributions() -> None:
    series = parameter_bootstrap_series(
        {
            "method": "moving_block_bootstrap",
            "windows": {
                "train": {"total_p05": -0.05, "total_p50": 0.15, "total_p95": 0.40},
                "recent": {"total_p05": -0.20, "total_p50": 0.02, "total_p95": 0.25},
            },
        }
    )

    assert series == {
        "train": {"p05": -0.05, "p50": 0.15, "p95": 0.40},
        "recent": {"p05": -0.20, "p50": 0.02, "p95": 0.25},
    }


def test_standard_parameter_selection_rejects_unproven_bootstrap_input() -> None:
    with pytest.raises(ValueError, match="moving_block_bootstrap"):
        select_standard_parameter(
            [_point(8)],
            {},
            bootstrap={"train": {"p05": -0.1, "p50": 0.1, "p95": 0.3}},
        )


@pytest.mark.parametrize(
    "curve",
    [
        [],
        [{"value": 1.0}],
        [{"value": 1.0}, {"value": 0.0}],
        [{"value": 1.0}, {"value": float("nan")}],
    ],
)
def test_bootstrap_equity_curve_rejects_invalid_source(curve: list[dict]) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        bootstrap_equity_curve(curve, seed=1)


def test_standard_parameter_selection_rejects_negative_validation_and_bad_constraints() -> None:
    result = select_standard_parameter(
        [
            _point(8, validation=-0.01),
            _point(10, plateau=False),
            _point(12, cost_monotonic=False),
            _point(14, trades=10),
            _point(16, validation=0.03, train=0.10),
        ],
        {"min_trades": 20, "max_mdd": -0.20},
    )
    selected = result["selected"]
    assert selected["parameter"] == 16
    rejected = {row["parameter"]: row["rejection_reasons"] for row in result["rejected"]}
    assert "validation_annual_positive" in rejected[8]
    assert "plateau_ok" in rejected[10]
    assert "cost_monotonic" in rejected[12]
    assert "min_trades" in rejected[14]


def test_standard_parameter_artifacts_create_three_pngs(tmp_path: Path) -> None:
    result = select_standard_parameter([_point(8), _point(10)], {})
    artifacts = build_standard_parameter_artifacts(result, tmp_path, prefix="parameter_mc")
    assert len(artifacts) == 3
    for item in artifacts:
        path = tmp_path / item["path"]
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert item["sha256"]
