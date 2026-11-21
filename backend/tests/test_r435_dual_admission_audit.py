from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r435_dual_admission_audit_preregistration.json"


def test_r435_freezes_dual_admission_rules() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R435"
    assert payload["rules"]["require_portfolio_ready"] is True
    assert payload["rules"]["require_research_admitted"] is True
    assert len(payload["current_set_members"]) == 8


def test_r435_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r435_current_set_only_has_three_dual_admitted_legs() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r435_dual_admission_audit_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["dual_admitted_count"] == 3
    assert set(payload["dual_admitted"]) == {
        "ext_alt_hs300_dividend_yield",
        "ext_alt_hs300_fund_holding_ratio_low",
        "ext_alt_hs300_idio_vol_40d_low",
    }
    assert payload["rows"]["ext_alt_hs300_cash_flow_stability_4q_low"]["portfolio_ready"] is False
    for name in ["r435_dual_admission_audit_preregistration.json", "r435_dual_admission_audit_2026-11-22.json", "r435_dual_admission_audit_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
