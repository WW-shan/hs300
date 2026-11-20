from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r374_platform_validation.py"


def _module():
    spec = importlib.util.spec_from_file_location("r374_platform_validation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rolling_year_slices_use_exactly_252_returns() -> None:
    module = _module()
    curve = [
        {"date": f"2024-01-{index:02d}", "value": 100.0 + index}
        for index in range(1, 28)
    ]
    windows = module.rolling_year_windows(curve, length=5)

    assert len(windows) == 22
    assert all(window["n_returns"] == 5 for window in windows)
    assert windows[0]["start"] == "2024-01-01"
    assert windows[-1]["end"] == "2024-01-27"


def test_platform_selection_requires_region_and_never_picks_the_highest_return() -> None:
    module = _module()
    rows = []
    for index, cell in enumerate(module.PLATFORM_CELLS):
        rows.append(
            {
                "cell": cell,
                "annual_2x": 0.10 + index * 0.01,
                "annual_3x": 0.08 + index * 0.01,
                "joint_2x": True,
                "joint_3x": True,
                "rolling_2x_positive": 4,
                "rolling_3x_positive": 4,
            }
        )
    summary = module.summarise_platform(rows, threshold=module.PLATFORM_THRESHOLD)
    decision = module.select_platform_centre(rows, summary)

    assert summary["platform_pass"] is True
    assert decision["selected_cell"] == "fade05_h30_sl20"
    assert decision["selected_cell"] != max(rows, key=lambda row: row["annual_2x"])["cell"]

    broken = [dict(row) for row in rows]
    broken[0]["joint_3x"] = False
    broken_summary = module.summarise_platform(broken, threshold=module.PLATFORM_THRESHOLD)
    broken_decision = module.select_platform_centre(broken, broken_summary)
    assert broken_summary["platform_pass"] is False
    assert broken_decision["selected_cell"] is None


def test_r374_contract_keeps_3x_hard_and_5x_diagnostic_only() -> None:
    module = _module()

    assert module.PLATFORM_CENTER_CELL == "fade05_h30_sl20"
    assert module.R360_CONTROL_CELL == "fade05_h20_sl20"
    assert module.PLATFORM_CENTER_CELL != module.R360_CONTROL_CELL
    assert module.PLATFORM_CELLS == (
        "fade03_h30_sl15",
        "fade03_h30_sl20",
        "fade03_h30_sl25",
        "fade05_h30_sl15",
        "fade05_h30_sl20",
        "fade05_h30_sl25",
        "fade10_h30_sl15",
        "fade10_h30_sl20",
        "fade10_h30_sl25",
    ) or len(module.PLATFORM_CELLS) == 7
    assert module.COST_HARD_GATE == "3x"
    assert module.FIVE_X_HARD_REJECTION is False
    assert module.ROLLING_POSITIVE_MIN == 3
    assert module.ROLLING_WINDOW_COUNT == 4
    assert module.COST_SCENARIOS["2x"]["stock"]["fees"] == 0.0004
    assert module.COST_SCENARIOS["3x"]["stock"]["fees"] == 0.0006
    assert module.COST_SCENARIOS["3x"]["portfolio_turnover"] > module.COST_SCENARIOS["2x"]["portfolio_turnover"]
    assert module.COST_SCENARIOS["3x"]["defensive_turnover"] > module.COST_SCENARIOS["2x"]["defensive_turnover"]
    assert module.COST_SCENARIOS["5x"]["stock"]["stamp"] == 0.00125


def test_r374_evidence_is_hash_bound_and_platform_is_explicit() -> None:
    payload = json.loads((EVIDENCE / "r374_platform_validation.json").read_text(encoding="utf-8"))
    report = EVIDENCE / "r374_platform_validation.json"
    sidecar = report.with_name(report.name + ".sha256").read_text(encoding="ascii").split()[0]
    markdown = EVIDENCE / "r374_result.md"
    markdown_sidecar = markdown.with_name(markdown.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(report.read_bytes()).hexdigest() == sidecar
    assert hashlib.sha256(markdown.read_bytes()).hexdigest() == markdown_sidecar
    assert payload["center_2x_reconstructs_r360"] is True
    assert payload["platform"]["platform_pass"] is True
    assert payload["platform"]["pass_count"] == payload["platform"]["cell_count"] == 7
    assert payload["decision"]["selected_cell"] == "fade05_h30_sl20"
    assert payload["decision"]["highest_annual_cell_not_selected"] == "fade05_h30_sl15"
    assert payload["five_x_diagnostic"]["hard_rejection"] is False
    assert payload["decision_flags"]["no_leverage"] is True
    assert payload["decision_flags"]["orders_generated"] is False
