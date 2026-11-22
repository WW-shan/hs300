from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r490_six_leg_ablation_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r490_six_leg_ablation.py"


def _module():
    spec = importlib.util.spec_from_file_location("r490_ablation", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r490_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R490"
    assert len(payload["source_members"]) == 6
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert (
        _sha(PREREG)
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r490_samples_remove_exactly_one_leg() -> None:
    module = _module()
    rows = module.generate_samples()
    source = json.loads(PREREG.read_text(encoding="utf-8"))["source_members"]
    assert len(rows) == 6
    for row in rows:
        assert len(row["weights"]) == 5
        assert len(set(source) - set(row["weights"])) == 1
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["max_new_positions_per_day"] == 1
        assert row["max_exposure_pct"] <= 1.0


def test_r490_outputs_are_confirmation_only() -> None:
    output = E / "r490_six_leg_ablation_2026-11-22.json"
    result = E / "r490_six_leg_ablation_2026-11-22.md"
    for path in (output, result):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["diagnostics"]["five_x_warning_only"] is True
    assert payload["decision"]["active_version"] is None
    assert len(payload["selected"]["weights"]) == 5
