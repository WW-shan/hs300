from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r385_pit_platform_monte_carlo.py"
PREREG = EVIDENCE / "r385_preregistration.json"
REPORT = EVIDENCE / "r385_pit_platform_monte_carlo.json"
RESULT = EVIDENCE / "r385_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r385_pit_platform_monte_carlo", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_r385_preregistration_preserves_no_leverage_and_cost_roles() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))

    assert payload["round"] == "R385"
    assert payload["frozen_design"]["alpha_grid"]["cells"] == 27
    assert len(payload["frozen_design"]["platform_cells"]) == 7
    assert payload["frozen_design"]["centre_cell"] == "fade05_h30_sl20"
    assert payload["frozen_design"]["control_cell"] == "fade05_h20_sl20"
    assert payload["cost_contract"]["2x"]["role"] == "hard primary gate"
    assert payload["cost_contract"]["3x"]["role"] == "hard cost-stress gate"
    assert payload["cost_contract"]["5x"]["role"].startswith("diagnostic warning only")
    assert payload["cost_contract"]["leverage"] == "none"
    assert payload["cost_contract"]["max_exposure_pct"] == 1.0


def test_grid_and_hypersample_contract_are_deterministic() -> None:
    module = _module()

    assert len(module.grid_cells()) == 27
    assert module.cell_slug(5, 30, -0.20) == "fade05_h30_sl20"
    first = module.latin_hypercube(24, 5, 379)
    second = module.latin_hypercube(24, 5, 379)
    assert first == second
    for dimension in range(5):
        column = sorted(row[dimension] for row in first)
        assert [int(value * 24) for value in column] == list(range(24))


def test_platform_requires_every_cell_and_never_selects_highest_annual() -> None:
    module = _module()
    rows = []
    for index, cell in enumerate(module.PLATFORM_CELLS):
        rows.append(
            {
                "cell": cell,
                "pass_2x": True,
                "pass_3x": True,
                "rolling_2x_positive": 4,
                "annual_2x": 0.10 + index * 0.01,
            }
        )

    result = module.evaluate_platform(rows)
    assert result["platform_pass"] is True
    assert result["selected_cell"] == module.CENTRE_CELL
    assert result["highest_annual_cell"] != module.CENTRE_CELL

    broken = [dict(row) for row in rows]
    broken[0]["pass_3x"] = False
    broken_result = module.evaluate_platform(broken)
    assert broken_result["platform_pass"] is False
    assert broken_result["selected_cell"] is None


def test_five_x_is_warning_not_a_hard_rejection() -> None:
    module = _module()
    result = module.evaluate_five_x(
        {"annual": 0.03, "sharpe": 0.5, "mdd": -0.30}
    )

    assert result["hard_rejection"] is False
    assert result["warning"] is True


def test_current_pit_lineage_must_match_r381_hashes() -> None:
    module = _module()
    payload = json.loads(PREREG.read_text(encoding="utf-8"))

    assert module.PREREG == PREREG
    assert payload["trigger"]["current_pit_source"].startswith("R381")
    assert payload["trigger"]["reason"].endswith("cannot be carried forward as current evidence.")
    assert payload["input_bindings"]["all_inputs_must_be_hash_verified_before_portfolio_returns_are_read"] is True


def test_broad_grid_uses_preregistered_overall_and_marginal_rates() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for cell in module.grid_cells():
        fade, hold, stop = module.cell_params(cell)
        rows.append({
            "cell": cell,
            "fade_days": fade,
            "hold_days": hold,
            "stop_loss": stop,
            "pass_2x": True,
        })

    result = module.evaluate_grid(rows, prereg)
    assert result["broad_grid_pass"] is True
    assert result["pass_count_2x"] == 27

    for row in rows[:7]:
        row["pass_2x"] = False
    failed = module.evaluate_grid(rows, prereg)
    assert failed["broad_grid_pass"] is False
    assert failed["pass_rate_2x"] < 0.75


def test_monte_carlo_requires_full_sealed_and_paired_advantage_rates() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for _ in range(96):
        rows.append({
            "centre": {
                "full": {"annual": 0.10, "sharpe": 1.2, "mdd": -0.10},
                "yearly": {"2022": -0.01, "2023": 0.05},
                "recent": {"total_return": 0.03},
                "sealed": {"total_return": 0.08, "sharpe": 1.2, "mdd": -0.05},
            },
            "control": {"sealed": {"total_return": 0.05}},
            "sealed_advantage": 0.03,
        })

    result = module.evaluate_monte_carlo(rows, prereg)
    assert result["parameter_plateau_pass"] is True
    assert result["paired_sealed_advantage_rate"] == 1.0

    for row in rows[:25]:
        row["centre"]["sealed"]["sharpe"] = 0.2
    failed = module.evaluate_monte_carlo(rows, prereg)
    assert failed["parameter_plateau_pass"] is False
    assert failed["sealed_pass_rate"] < 0.75


def test_cohort_combiner_receives_full_curve_frames_not_first_columns() -> None:
    module = _module()
    received = []

    class FakeR359:
        @staticmethod
        def combine_cohort_curves(curves, starts):
            received.extend(curves)
            assert len(curves) == len(starts) == 3
            assert all(isinstance(curve, module.pl.DataFrame) for curve in curves)
            assert all(curve.columns == ["date", "value"] and curve.height == 2 for curve in curves)
            return curves[0]

    outputs = {
        f"C_2x_{index}": module.pl.DataFrame({
            "date": [
                module.date(2022, 5, 5) + timedelta(days=index),
                module.date(2022, 5, 6) + timedelta(days=index),
            ],
            "value": [1.0, 1.1],
        })
        for index in range(3)
    }
    module.combine_c(outputs, "2x", {"r359": FakeR359})
    assert len(received) == 3


def test_failed_monte_carlo_samples_and_exposure_are_explicit() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = []
    for index in range(4):
        rows.append({
            "sample": index,
            "centre": {
                "full": {"annual": 0.10, "sharpe": 1.2, "mdd": -0.10},
                "yearly": {"2022": -0.01, "2023": 0.05},
                "recent": {"total_return": 0.03},
                "sealed": {
                    "total_return": 0.08,
                    "sharpe": 0.5 if index == 1 else 1.2,
                    "mdd": -0.05,
                },
            },
            "control": {"sealed": {"total_return": 0.05}},
            "sealed_advantage": 0.03,
        })

    result = module.evaluate_monte_carlo(rows, prereg)
    assert result["failed_sealed_samples"] == [1]
    assert module.exposures_within_limit([1.0, 0.75, 1.0 + 1e-13]) is True
    assert module.exposures_within_limit([1.0, 1.01]) is False


def test_r385_committed_evidence_matches_final_schema_and_sidecars() -> None:
    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256")
        expected = sidecar.read_text(encoding="ascii").split()[0]
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected

    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["round"] == "R385"
    assert report["preregistration_sha256"] == hashlib.sha256(PREREG.read_bytes()).hexdigest()
    assert report["all_pass"] is True
    assert report["no_leverage"] is True
    assert report["decision"]["status"] == "candidate_for_paper_trading_review"
    assert report["decision"]["selected_cell"] == prereg["frozen_design"]["centre_cell"]
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["production_enabled"] is False

    grid = report["broad_grid"]
    assert grid["pass_count_2x"] == grid["cell_count"] == 27
    assert grid["minimum_marginal_pass_rate_2x"] == 1.0
    platform = report["platform"]
    assert platform["platform_pass"] is True
    assert platform["selected_cell"] == prereg["frozen_design"]["centre_cell"]
    assert platform["selected_cell_is_highest_annual"] is False

    monte_carlo = report["monte_carlo"]
    assert monte_carlo["sample_count"] == 96
    assert monte_carlo["full_pass_count"] == 96
    assert monte_carlo["sealed_pass_count"] == 94
    assert monte_carlo["paired_sealed_advantage_count"] == 96
    assert monte_carlo["failed_full_samples"] == []
    assert monte_carlo["failed_sealed_samples"] == [2, 62]
    assert monte_carlo["failed_advantage_samples"] == []
    assert monte_carlo["parameter_plateau_pass"] is True
    assert monte_carlo["advantage_worst"] > 0.0

    grid_exposures = [
        row["exposure"][cost]["max_weight_sum"]
        for row in report["grid_rows"]
        for cost in ("2x", "3x")
    ]
    mc_exposures = [
        row[label]["max_weight_sum"]
        for row in report["monte_carlo_rows"]
        for label in ("centre", "control")
    ]
    assert grid_exposures and mc_exposures
    assert all(value <= 1.0 + 1e-12 for value in grid_exposures + mc_exposures)
    assert report["five_x_diagnostic"]["max_weight_sum"] <= 1.0 + 1e-12
    assert report["five_x_diagnostic"]["hard_rejection"] is False
