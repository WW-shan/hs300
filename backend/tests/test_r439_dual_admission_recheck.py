from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"


def test_r439_dual_admitted_count_is_four() -> None:
    payload = json.loads((E / "r439_dual_admission_recheck_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["dual_admitted_count"] == 4
    assert "ext_alt_hs300_cash_flow_stability_4q_low" in payload["dual_admitted"]
    assert "ext_alt_hs300_ocf_margin_4q" not in payload["dual_admitted"]


def test_r439_sidecars() -> None:
    for name in ["r439_dual_admission_recheck_preregistration.json", "r439_dual_admission_recheck_2026-11-22.json", "r439_dual_admission_recheck_2026-11-22.md"]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
