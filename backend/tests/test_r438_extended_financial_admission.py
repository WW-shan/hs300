from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r438_extended_financial_research_admission_preregistration.json"


def test_r438_freezes_two_candidates_and_rules() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R438"
    assert payload["candidates"] == [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_ocf_margin_4q",
    ]
    assert payload["rules"]["yearly_delta_sign_consistency_min"] == 0.80
    assert payload["horizons"] == [5, 20, 60]


def test_r438_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r438_only_cash_flow_stability_is_admitted() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r438_extended_financial_research_admission_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["admitted"] == ["ext_alt_hs300_cash_flow_stability_4q_low"]
    assert payload["rows"]["ext_alt_hs300_cash_flow_stability_4q_low"]["passing_horizons"] == [60]
    assert payload["rows"]["ext_alt_hs300_ocf_margin_4q"]["passing_horizons"] == []
    for name in ["r438_extended_financial_research_admission_preregistration.json", "r438_extended_financial_research_admission_2026-11-22.json", "r438_extended_financial_research_admission_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
