from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r375_platform_walkforward.py"
REPORT = EVIDENCE / "r375_platform_walkforward.json"


def _module():
    spec = importlib.util.spec_from_file_location("r375_platform_walkforward", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grid_and_neighbour_contract_are_exact() -> None:
    module = _module()

    assert len(module.grid_cells()) == 27
    assert module.FIXED_CENTRE == "fade05_h30_sl20"
    assert module.cell_slug(5, 30, -0.20) == module.FIXED_CENTRE
    assert "fade03_h30_sl20" in module.axis_neighbours("fade05_h30_sl20")
    assert "fade10_h30_sl20" in module.axis_neighbours("fade05_h30_sl20")
    assert "fade05_h20_sl20" in module.axis_neighbours("fade05_h30_sl20")
    assert "fade05_h30_sl15" in module.axis_neighbours("fade05_h30_sl20")
    assert module.cell_params("fade10_h10_sl25") == (10, 10, -0.25)


def test_selectors_only_use_trailing_metrics() -> None:
    module = _module()
    cells = module.grid_cells()
    metrics = {
        cell: {"annual": 0.10, "sharpe": 1.10, "mdd": -0.10, "total_return": 0.10}
        for cell in cells
    }
    chosen_before = {
        name: module.SELECTORS[name](metrics)
        for name in module.POLICIES
    }
    # Mutating an unrelated future field must not change the selector.
    for cell in cells:
        metrics[cell]["future_return"] = -0.99
    chosen_after = {
        name: module.SELECTORS[name](metrics)
        for name in module.POLICIES
    }
    assert chosen_before == chosen_after
    assert chosen_after["P5_fixed_centre"] == module.FIXED_CENTRE


def test_platform_medoid_falls_back_when_too_few_cells_pass() -> None:
    module = _module()
    metrics = {
        cell: {"annual": 0.04, "sharpe": 0.5, "mdd": -0.25, "total_return": 0.04}
        for cell in module.grid_cells()
    }
    assert module.select_platform_medoid(metrics) == module.FIXED_CENTRE


def test_decision_dates_require_full_trailing_and_forward_windows() -> None:
    module = _module()
    weekdays = [date(2023, 1, 2)]
    import datetime

    day = weekdays[0]
    while len(weekdays) < 703:
        day += datetime.timedelta(days=1)
        weekdays.append(day)
    points = module.decision_dates(weekdays)
    assert points
    for decision in points:
        index = weekdays.index(decision)
        assert index >= module.TRAILING
        assert index + module.FORWARD < len(weekdays)


def test_r375_evidence_is_hash_bound_and_decision_is_explicit() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for path in (
        EVIDENCE / "r375_preregistration.json",
        REPORT,
        EVIDENCE / "r375_result.md",
    ):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R375"
    assert payload["decision_points"] == 29
    assert payload["trailing_length"] == payload["forward_length"] == 252
    assert payload["fixed_centre"] == "fade05_h30_sl20"
    assert payload["decision"]["verdict"] in {
        "platform_selection_supported",
        "platform_claim_limited_to_parameter_robustness",
        "no_ex_ante_selector_adds_value_fixed_centre_remains_default",
    }
    assert payload["decision"]["secondary_maximin_beats_trailing_on_mean_and_worst"] is True
    assert payload["decision"]["no_leverage"] is True
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["production_enabled"] is False
