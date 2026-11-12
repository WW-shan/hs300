from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/"
    "run_r371_alpha_parameter_grid.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r371_alpha_grid", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parameter_grid_is_complete_and_contains_center() -> None:
    module = _module()

    combinations = module.parameter_grid()

    assert len(combinations) == 27
    assert len(set(combinations)) == 27
    assert (5, 20, -0.20) in combinations


def test_generated_factor_ids_match_registry_contract() -> None:
    module = _module()

    identifiers = [module.factor_id(fade) for fade in module.FADE_DAYS]

    assert identifiers == [
        "uf_r371_fade_change_3d",
        "uf_r371_fade_change_5d",
        "uf_r371_fade_change_10d",
    ]
    assert all(re.fullmatch(r"uf_[a-z0-9_]{1,40}", identifier) for identifier in identifiers)


def test_grid_summary_checks_every_marginal_slice() -> None:
    module = _module()
    rows = [
        {"fade_days": fade, "hold_days": hold, "stop_loss": stop, "joint_pass": True}
        for fade, hold, stop in module.parameter_grid()
    ]

    summary = module.summarise_grid(rows)

    assert summary["pass_count"] == 27
    assert summary["pass_rate"] == 1.0
    assert summary["plateau_pass"] is True
    assert summary["marginal_fade_days"] == {"3": 1.0, "5": 1.0, "10": 1.0}

    rows[0]["joint_pass"] = False
    weak = module.summarise_grid(rows)
    assert weak["pass_count"] == 26
    assert weak["plateau_pass"] is True

    for row in rows:
        row["joint_pass"] = False
    failed = module.summarise_grid(rows)
    assert failed["plateau_pass"] is False
