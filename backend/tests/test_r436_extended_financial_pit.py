from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r436_extended_financial_pit_preregistration.json"


def test_r436_freezes_two_extended_financial_factors() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R436"
    assert payload["factors"] == ["ext_alt_hs300_cash_flow_stability_4q_low", "ext_alt_hs300_ocf_margin_4q"]
    assert payload["checks"]["prefix_invariance"] is True


def test_r436_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]
