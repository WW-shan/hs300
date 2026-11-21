from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/factor_research/research"
PREREG = EVIDENCE / "r402_library_preregistration.json"


def test_r402_is_capped_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R402"
    assert payload["design"]["capacity_limit"] == 8
    assert payload["output_policy"]["active_version"] is None
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar


def test_r402_result_discloses_dilution() -> None:
    payload = json.loads((EVIDENCE / "r402_library_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "library_expansion_complete_no_promotion"
    assert len(payload["members"]) == 8
    assert payload["grid"]
    assert all(row["hs300_excess"] is not None for row in payload["grid"])
