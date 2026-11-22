from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r476_factor_set_expansion_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r476_factor_set_expansion_mc.py"
OUTPUT = E / "r476_factor_set_expansion_mc_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r476_expand", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r476_preregistration_freezes_candidates() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R476"
    assert len(payload["base_members"]) == 8
    assert len(payload["candidates"]) >= 2
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["cost_main"] == "2x"
    assert payload["execution"]["cost_stress"] == "3x"
    assert payload["execution"]["cost_diagnostic"] == "5x"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["execution"]["no_leverage"] is True
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r476_samples_are_normalized_and_complete() -> None:
    module = _module()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = module.generate_samples()
    assert rows == module.generate_samples()
    assert len(rows) == len(prereg["candidates"]) * len(prereg["added_weight_grid"])
    for row in rows:
        weights = row["weights"]
        directions = row["directions"]
        assert set(weights) == set(prereg["base_members"]) | {row["added_factor"]}
        assert set(directions) == set(weights)
        assert abs(sum(weights.values()) - 1.0) < 1e-12
        assert weights[row["added_factor"]] == row["added_weight"]
        assert directions == {factor: prereg["directions"][factor] for factor in weights}
        assert 0.0 < row["max_exposure_pct"] <= 1.0
        assert row["top_rank"] >= row["max_positions"]


def test_r476_outputs_are_confirmation_only() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r476_cost_scenarios_are_distinct() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    assert selected["test"]["stats"]["annual_return"] != selected["test_3x"]["stats"]["annual_return"]
    assert selected["test_3x"]["stats"]["annual_return"] != selected["test_5x"]["stats"]["annual_return"]
    assert selected["test_3x"]["stats"]["annual_return"] > 0.0


def test_r476_selected_sample_really_expands_the_library() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert len(selected["weights"]) == len(prereg["base_members"]) + 1
    assert selected["added_factor"] in selected["weights"]
    assert abs(sum(selected["weights"].values()) - 1.0) < 1e-12
    assert selected["directions"] == {
        factor: prereg["directions"][factor] for factor in selected["weights"]
    }
    assert 0.0 < selected["max_exposure_pct"] <= 1.0
    assert selected["top_rank"] >= selected["max_positions"]
