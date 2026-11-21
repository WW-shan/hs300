from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r441_margin_research_admission_preregistration.json"


def test_r441_freezes_margin_only() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R441"
    assert payload["candidate"] == "ext_alt_hs300_margin_chg_1m_low"
    assert payload["horizons"] == [5, 20, 60]
    assert payload["already_rejected"] == ["ext_alt_hs300_holder_top10_share_chg_yoy"]


def test_r441_sidecar() -> None:
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == PREREG.with_name(
        PREREG.name + ".sha256"
    ).read_text().split()[0]


def test_r441_margin_rejected_on_yearly_consistency() -> None:
    output = E / "r441_margin_research_admission_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["admitted"] is False
    assert payload["passing_horizons"] == []
    assert payload["rows"]["5"]["yearly_delta_sign_consistency"] < 0.80
    for name in [
        "r441_margin_research_admission_preregistration.json",
        "r441_margin_research_admission_2026-11-22.json",
        "r441_margin_research_admission_2026-11-22.md",
    ]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
