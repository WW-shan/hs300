from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r474_panic_reversal_standalone_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r474_panic_reversal_standalone.py"
OUTPUT = E / "r474_panic_reversal_standalone_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r474_panic", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r474_preregistration_freezes_factor_and_costs() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R474"
    assert payload["factor"] == "new_panic_reversal_5d"
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r474_samples_are_deterministic_and_bounded() -> None:
    module = _module()
    rows = module.generate_samples()
    assert rows == module.generate_samples()
    assert len(rows) == 486
    for row in rows:
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["lookback"] in {5, 10, 20}
        assert row["volume_threshold"] in {1.2, 1.5, 2.0}


def test_r474_outputs_are_confirmation_only() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r474_panic_factor_has_sufficient_training_coverage() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["sample_count"] == 486
    assert payload["diagnostics"]["factor_non_null_rows"] > 0
    assert payload["diagnostics"]["factor_non_null_ratio"] >= 0.01
