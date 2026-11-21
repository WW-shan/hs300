from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r420_final_candidate_tail_preregistration.json"


def test_r420_freezes_two_candidates_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R420"
    assert set(payload["candidates"]) == {"compact_4f", "current_8f"}
    assert payload["cost_protocol"]["five_x_hard_rejection"] is False
    assert payload["output_policy"]["active_version"] is None


def test_r420_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r420_marks_compact_candidate_rejected_on_tail_failure() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r420_final_candidate_tail_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["gate"]["pass"] == all(payload["gate"]["checks"].values())
    if not payload["gate"]["checks"]["compact_test_total_p05_positive"]:
        assert payload["gate"]["pass"] is False
    for name in ["r420_final_candidate_tail_preregistration.json", "r420_final_candidate_tail_2026-11-22.json", "r420_final_candidate_tail_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
