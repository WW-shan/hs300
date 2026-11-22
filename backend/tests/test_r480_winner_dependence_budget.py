from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r480_winner_dependence_budget_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r480_winner_dependence_budget.py"


def _module():
    spec = importlib.util.spec_from_file_location("r480_winner", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r480_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R480"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert payload["dependence_metric"]["not_used_on_test_or_recent_for_selection"] is True
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r480_subsets_are_eight_leg_and_bounded() -> None:
    module = _module()
    rows = module.generate_samples()
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert len(rows) == len(payload["subsets"])
    for row in rows:
        assert len(row["weights"]) == 8
        assert set(row["weights"]) == set(payload["subsets"][row["label"]])
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert 0.0 < row["max_exposure_pct"] <= 1.0


def test_r480_dependence_metric_is_bounded() -> None:
    module = _module()
    assert module.top_positive_share([]) == 0.0
    assert module.top_positive_share([{"pnl_amount": 1.0}, {"pnl_amount": -1.0}]) == 1.0
    assert module.top_positive_share([{"pnl_amount": 1.0}, {"pnl_amount": 3.0}]) == 1.0
    assert 0.0 <= module.top_positive_share([{"pnl_amount": 1.0}, {"pnl_amount": 2.0}, {"pnl_amount": 3.0}, {"pnl_amount": 4.0}, {"pnl_amount": 5.0}, {"pnl_amount": 6.0}]) <= 1.0


def test_r480_outputs_are_confirmation_only_and_bounded() -> None:
    output = E / "r480_winner_dependence_budget_2026-11-22.json"
    result = E / "r480_winner_dependence_budget_2026-11-22.md"
    for path in (output, result):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["diagnostics"]["no_leverage"] is True
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}
    assert len(payload["selected"]["weights"]) == 8
    assert abs(sum(payload["selected"]["weights"].values()) - 1.0) < 1e-12
    assert payload["selected"]["max_exposure_pct"] <= 1.0


def test_r480_selected_subset_has_lower_top5_share_than_r476_diagnostic() -> None:
    payload = json.loads((E / "r480_winner_dependence_budget_2026-11-22.json").read_text(encoding="utf-8"))
    dependency = payload["selected"]["test_dependence"]
    assert 0.0 <= dependency["top_5_positive_share"] <= 1.0
    assert dependency["top_5_positive_share"] < 0.5
