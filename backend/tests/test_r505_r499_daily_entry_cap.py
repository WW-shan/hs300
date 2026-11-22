from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r505_r499_daily_entry_cap_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r505_r499_daily_entry_cap.py"


def _module():
    spec = importlib.util.spec_from_file_location("r505_cap", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_r505_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R505"
    assert payload["caps"] == [None, 1, 2, 3]
    assert payload["fixed_execution"]["max_positions"] == 10
    assert payload["fixed_execution"]["max_hold_days"] == 25
    assert payload["source_r499"] == "r499_weak_state_value_quality_tilt_2026-11-22.json"
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r505_candidate_labels() -> None:
    module = _module()
    rows = module.generate_samples()
    assert [row["label"] for row in rows] == ["baseline", "cap_1", "cap_2", "cap_3"]
    assert all(abs(sum(row["weights"].values()) - 1.0) < 1e-12 for row in rows)
    assert all(row["fixed_execution"]["no_leverage"] is True for row in rows)


def test_r505_outputs_are_confirmation_only() -> None:
    output = E / "r505_r499_daily_entry_cap_2026-11-22.json"
    result = E / "r505_r499_daily_entry_cap_2026-11-22.md"
    for path in (output, result):
        assert (
            hashlib.sha256(path.read_bytes()).hexdigest()
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["decision"]["active_version"] is None
