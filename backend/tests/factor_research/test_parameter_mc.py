from __future__ import annotations

from datetime import date

import polars as pl

from app.factor_research.parameter_mc import (
    annotate_plateau,
    choose_recommended_candidate,
    select_robust_candidates,
)


def _trial(
    parameter: int,
    *,
    full: float = 0.04,
    train: float = 0.05,
    test: float = 0.03,
    neutral: float = 0.03,
    ci_low: float = 0.01,
    q_value: float = 0.05,
    plateau: bool = True,
) -> dict:
    return {
        "parameter": parameter,
        "full": {"directional_ic": full, "p_value": 0.01},
        "train": {"directional_ic": train},
        "test": {"directional_ic": test},
        "industry_neutral": {"full": {"p_value": 0.02}},
        "size_neutral": {"full": {"p_value": 0.02}},
        "industry_size_neutral": {
            "full": {"directional_ic": neutral, "p_value": 0.02}
        },
        "bootstrap_full": {"ci_low": ci_low},
        "q_value": q_value,
        "robust_plateau": plateau,
    }


def test_select_robust_candidates_applies_all_initial_gates() -> None:
    trials = [
        _trial(20),
        _trial(40, ci_low=-0.01),
        _trial(60, test=-0.01),
        _trial(120, q_value=0.2),
    ]

    assert [row["parameter"] for row in select_robust_candidates(trials)] == [20]


def test_choose_recommended_candidate_uses_final_ci_then_distance() -> None:
    trials = [
        {**_trial(20), "final_bootstrap": {"20": {"ci_low": 0.02}}},
        {**_trial(60), "final_bootstrap": {"20": {"ci_low": 0.03}}},
        {**_trial(120), "final_bootstrap": {"20": {"ci_low": 0.03}}},
    ]

    assert choose_recommended_candidate(
        trials, current_parameter=120
    )["parameter"] == 120
    assert choose_recommended_candidate(
        trials, current_parameter=20
    )["parameter"] == 60


def test_choose_recommended_candidate_rejects_negative_final_ci() -> None:
    trials = [{**_trial(20), "final_bootstrap": {"20": {"ci_low": -0.001}}}]

    assert choose_recommended_candidate(trials, current_parameter=20) is None


def test_annotate_plateau_requires_positive_inward_neighbors() -> None:
    trials = [_trial(20), _trial(40), _trial(60, full=-0.01), _trial(120)]

    annotate_plateau(trials)

    assert trials[0]["robust_plateau"] is False
    assert trials[1]["robust_plateau"] is False
    assert trials[3]["robust_plateau"] is False


def test_assign_family_q_values_preserves_missing_values() -> None:
    from app.factor_research.parameter_mc import assign_family_q_values

    trials = [_trial(20), _trial(40)]
    trials[1]["full"]["p_value"] = None
    trials[1]["industry_neutral"]["full"]["p_value"] = None
    trials[1]["size_neutral"]["full"]["p_value"] = None
    trials[1]["industry_size_neutral"]["full"]["p_value"] = None

    assign_family_q_values(trials)

    assert trials[0]["q_value"] is not None
    assert trials[1]["q_value"] is None


def test_stats_and_bootstrap_are_deterministic() -> None:
    from app.factor_research.parameter_mc import bootstrap_ic, stats_for_period

    frame = pl.DataFrame({
        "date": [date(2024, 1, day) for day in range(1, 11)],
        "n": [30] * 10,
        "ic": [0.01, 0.02, 0.03, 0.01, 0.02, 0.03, 0.01, 0.02, 0.03, 0.02],
    })

    first = stats_for_period(
        frame, date(2024, 1, 1), date(2024, 1, 10), 1, include_distribution=True
    )
    second = stats_for_period(
        frame, date(2024, 1, 1), date(2024, 1, 10), 1, include_distribution=True
    )
    boot_a = bootstrap_ic(
        frame,
        date(2024, 1, 1),
        date(2024, 1, 10),
        1,
        block_size=2,
        n_bootstrap=100,
        seed=7,
    )
    boot_b = bootstrap_ic(
        frame,
        date(2024, 1, 1),
        date(2024, 1, 10),
        1,
        block_size=2,
        n_bootstrap=100,
        seed=7,
    )

    assert first == second
    assert first["yearly"][0]["year"] == 2024
    assert boot_a == boot_b
