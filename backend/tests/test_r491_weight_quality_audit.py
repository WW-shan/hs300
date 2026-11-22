from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r491_weight_quality_audit_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r491_weight_quality_audit.py"


def _module():
    spec = importlib.util.spec_from_file_location("r491_weights", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r491_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R491"
    assert len(payload["members"]) == 5
    assert payload["fixed_execution"]["max_new_positions_per_day"] == 1
    assert payload["fixed_execution"]["no_leverage"] is True
    assert payload["benchmark"] == "000300.SH"
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r491_weight_rules_are_nonnegative_and_normalized() -> None:
    module = _module()
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    supports = {
        "equal": {member: 1.0 for member in payload["members"]},
        "train_ic_positive": {member: 2.0 for member in payload["members"]},
        "train_validation_ic_positive": {member: 3.0 for member in payload["members"]},
        "stability_weighted": {member: 1.5 for member in payload["members"]},
    }
    for rule in payload["weight_rules"]:
        weights = module.normalized_weights(supports[rule])
        assert abs(sum(weights.values()) - 1.0) < 1e-12
        assert all(value >= 0.0 for value in weights.values())
        assert set(weights) == set(payload["members"])
