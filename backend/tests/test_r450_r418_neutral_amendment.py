from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r450_r418_neutral_amendment_preregistration.json"
OUTPUT = E / "r450_r418_neutral_amendment_2026-11-22.json"


def test_r450_sidecars() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]


def test_r450_is_downgrade_evidence_not_promotion() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["round"] == "R450"
    assert payload["admitted_count"] == 2
    assert payload["admitted"] == [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_vol_of_vol_60d_low",
    ]
    assert payload["rows"]["ext_alt_hs300_dividend_yield"]["admitted_under_amendment"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
