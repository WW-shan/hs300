from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r481_entry_exit_neighborhood_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r481_entry_exit_neighborhood.py"
R480 = E / "r480_winner_dependence_budget_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r481_neighborhood", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r481_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R481"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["benchmark"] == "000300.SH"
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r481_samples_vary_only_entry_exit() -> None:
    module = _module()
    r480 = json.loads(R480.read_text(encoding="utf-8"))
    rows = module.generate_samples()
    base = r480["selected"]
    assert rows == module.generate_samples()
    assert len(rows) == 9
    for row in rows:
        assert row["weights"] == base["weights"]
        assert row["directions"] == base["directions"]
        assert row["max_positions"] == base["max_positions"]
        assert row["max_hold_days"] == base["max_hold_days"]
        assert row["max_exposure_pct"] == base["max_exposure_pct"]
        assert row["top_rank"] == base["top_rank"]
        assert (row["entry_score"], row["exit_score"]) in {
            tuple(pair) for pair in json.loads(PREREG.read_text(encoding="utf-8"))["entry_exit_grid"]
        }


def test_r481_outputs_are_confirmation_only() -> None:
    output = E / "r481_entry_exit_neighborhood_2026-11-22.json"
    result = E / "r481_entry_exit_neighborhood_2026-11-22.md"
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
