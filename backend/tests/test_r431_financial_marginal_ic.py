from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r431_financial_marginal_ic_preregistration.json"


def test_r431_freezes_candidates_and_horizons() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R431"
    assert payload["horizons"] == [5, 20, 60]
    assert "fcf_proxy_to_revenue_ttm" in payload["candidates"]
    assert payload["output_policy"]["active_version"] is None


def test_r431_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r431_fcf_has_no_marginal_gain_over_eight_legs() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r431_financial_marginal_ic_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["pass_count"] == 0
    for factor in ["fcf_proxy_to_revenue_ttm", "ocf_to_revenue_ttm"]:
        assert payload["rows"][factor]["passing_horizons"] == []
    for name in ["r431_financial_marginal_ic_preregistration.json", "r431_financial_marginal_ic_2026-11-22.json", "r431_financial_marginal_ic_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
