from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r473_operating_efficiency_incremental_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r473_operating_efficiency_incremental.py"
OUTPUT = E / "r473_operating_efficiency_incremental_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r473_op_inc", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r473_preregistration_freezes_weights_and_gates() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R473"
    assert payload["op_factor"] == "inventory_growth_minus_cogs_growth"
    assert payload["weight_grid"] == [0.1, 0.2, 0.3]
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r473_samples_sum_to_one() -> None:
    module = _module()
    rows = module.generate_samples()
    assert len(rows) == 3
    for row in rows:
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["op_weight"] in {0.1, 0.2, 0.3}


def test_r473_outputs_are_confirmation_only() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r473_stress_cost_is_distinct() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["selected"]["test"]["stats"]["annual_return"] != payload["selected"]["test_3x"]["stats"]["annual_return"]
    assert payload["selected"]["test_3x"]["stats"]["annual_return"] > 0.0
