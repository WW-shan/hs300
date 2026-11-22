from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r475_earnings_surprise_standalone_preregistration.json"
RUNNER = ROOT / "scripts" / "research_r475_earnings_surprise_standalone.py"
OUTPUT = E / "r475_earnings_surprise_standalone_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r475_pead", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r475_preregistration_freezes_factors_and_costs() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R475"
    assert set(payload["sleeves"]) == {"forecast", "express", "combined"}
    assert payload["selection"]["basis"] == "train_and_validation_only"
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r475_samples_are_deterministic_and_bounded() -> None:
    module = _module()
    rows = module.generate_samples()
    assert rows == module.generate_samples()
    assert len(rows) == 162
    for row in rows:
        assert abs(sum(row["weights"].values()) - 1.0) < 1e-12
        assert row["sleeve"] in {"forecast", "express", "combined"}


def test_r475_outputs_are_confirmation_only() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["selection_basis"] == "train_and_validation_only"
    assert payload["test_used_for_selection"] is False
    assert payload["recent_used_for_selection"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}


def test_r475_coverage_is_not_empty_and_stress_is_distinct() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert all(row["non_null_ratio"] > 0.99 for row in payload["coverage"].values())
    assert payload["selected"]["test"]["stats"]["annual_return"] != payload["selected"]["test_3x"]["stats"]["annual_return"]
