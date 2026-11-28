from __future__ import annotations

from pathlib import Path

from app.factor_research.standard_parameter_mc import (
    build_standard_parameter_artifacts,
    select_standard_parameter,
)


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
