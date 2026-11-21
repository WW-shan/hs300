from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r429_financial_quality_gap_preregistration.json"


def test_r429_freezes_quality_gap_candidates() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R429"
    assert len(payload["candidates"]) >= 10
    assert payload["horizons"] == [5, 20, 60]
    assert payload["output_policy"]["active_version"] is None


def test_r429_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r429_fcf_and_ocf_revenue_pass_all_horizons() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r429_financial_quality_gap_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["rows"]["fcf_proxy_to_revenue_ttm"]["passing_horizons"] == [5, 20, 60]
    assert payload["rows"]["ocf_to_revenue_ttm"]["passing_horizons"] == [5, 20, 60]
    for name in ["r429_financial_quality_gap_preregistration.json", "r429_financial_quality_gap_2026-11-22.json", "r429_financial_quality_gap_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
