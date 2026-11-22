from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r465_factor_library_mc_tail_diagnosis_preregistration.json"
OUTPUT = E / "r465_factor_library_mc_tail_diagnosis_2026-11-22.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r465_is_descriptive_only_and_sidecar_bound() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R465"
    assert payload["rules"]["selection_allowed"] is False
    assert payload["rules"]["test_or_recent_used_for_new_parameters"] is False
    assert payload["rules"]["five_x_hard_rejection"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r465_diagnoses_r464_without_changing_decision() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["source_round"] == "R464"
    assert payload["source_sample_label"] == "lhs_044"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "factor_library_mc_tail_diagnosis_complete_no_promotion"
    assert payload["monthly"]["validation"]["worst_month"] is not None
    assert set(payload["bootstrap_sensitivity"]) == {"validation", "test", "recent"}
    assert all(set(row) == {"10", "20", "40"} for row in payload["bootstrap_sensitivity"].values())
