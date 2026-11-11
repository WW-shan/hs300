from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"


def _load_scan_module() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / "research_alt_factor_parameters.py"
    spec = importlib.util.spec_from_file_location("research_alt_factor_parameters_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scan = _load_scan_module()


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
        "full": {"directional_ic": full},
        "train": {"directional_ic": train},
        "test": {"directional_ic": test},
        "industry_size_neutral": {"full": {"directional_ic": neutral}},
        "bootstrap_full": {"ci_low": ci_low},
        "q_value": q_value,
        "robust_plateau": plateau,
    }


def test_load_p1_families_is_registry_driven_and_contains_eight_families() -> None:
    families = scan.load_p1_families(ROOT / "data")

    assert {family.family_id for family in families} == {
        "idio_vol",
        "cs_spread",
        "vol_of_vol",
        "market_down_beta",
        "price_delay",
        "trend_efficiency",
        "risk_adjusted_momentum",
        "beta_asymmetry",
    }
    assert all(family.status == "tunable" for family in families)


def test_select_robust_candidates_requires_all_initial_evidence() -> None:
    trials = [
        _trial(20),
        _trial(40, ci_low=-0.01),
        _trial(60, train=-0.01),
        _trial(120, q_value=0.2),
        _trial(180, neutral=-0.01),
    ]

    selected = scan.select_robust_candidates(trials)

    assert [item["parameter"] for item in selected] == [20]


def test_choose_recommended_candidate_uses_min_ic_then_final_ci_then_distance() -> None:
    trials = [
        {
            **_trial(20),
            "final_bootstrap": {"20": {"ci_low": 0.02}},
        },
        {
            **_trial(60),
            "final_bootstrap": {"20": {"ci_low": 0.03}},
        },
        {
            **_trial(120),
            "final_bootstrap": {"20": {"ci_low": 0.03}},
        },
    ]

    assert scan.choose_recommended_candidate(trials, current_parameter=120)["parameter"] == 120
    assert scan.choose_recommended_candidate(trials, current_parameter=20)["parameter"] == 60


def test_final_secondary_bootstrap_must_remain_positive() -> None:
    trials = [
        {
            **_trial(20),
            "final_bootstrap": {"20": {"ci_low": -0.001}},
        }
    ]

    assert scan.choose_recommended_candidate(trials, current_parameter=20) is None


def test_plateau_requires_two_inward_neighbors_at_grid_edge() -> None:
    trials = [
        _trial(20),
        _trial(40),
        _trial(60, full=-0.01),
        _trial(120),
    ]

    scan.annotate_plateau(trials)

    assert trials[0]["robust_plateau"] is False
    assert trials[1]["robust_plateau"] is False
    assert trials[3]["robust_plateau"] is False


def test_parameter_output_refuses_absolute_path_outside_research_directory(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="parameter output"):
        scan._resolve_output(str(tmp_path / "result.json"))
