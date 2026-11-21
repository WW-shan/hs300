from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r437_extended_financial_future_data_preregistration.json"


def test_r437_freezes_future_data_checks() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R437"
    assert payload["required_evidence"]["builder_uses_statutory_deadline"] is True
    assert payload["required_evidence"]["join_asof_backward_only"] is True


def test_r437_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r437_clear_status_is_explicit_in_taxonomy() -> None:
    taxonomy = json.loads(
        (ROOT / "data" / "factor_research" / "catalog" / "field_taxonomy.json").read_text(encoding="utf-8")
    )
    by_id = {row["factor_id"]: row for row in taxonomy["records"]}
    for factor in [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_ocf_margin_4q",
    ]:
        assert by_id[factor]["future_data_status"] == "clear"


def test_r437_blocked_reason_is_cleared_after_evidence() -> None:
    registry = json.loads(
        (ROOT / "data" / "factor_research" / "parameters" / "families.json").read_text(encoding="utf-8")
    )
    blocked = set(registry.get("blocked_factor_ids", []))
    fixed = set(registry.get("fixed_factor_ids", []))
    for factor in [
        "ext_alt_hs300_cash_flow_stability_4q_low",
        "ext_alt_hs300_ocf_margin_4q",
    ]:
        assert factor not in blocked
        assert factor in fixed
