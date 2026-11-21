from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r430_fcf_quality_portfolio_preregistration.json"


def test_r430_freezes_three_variants_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R430"
    assert len(payload["variants"]) == 3
    assert payload["execution"]["no_leverage"] is True
    assert payload["directions"]["fcf_proxy_to_revenue_ttm"] == "high"


def test_r430_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r430_rejects_if_fcf_variants_do_not_beat_baseline() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r430_fcf_quality_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_variant"] == "baseline"
    assert payload["decision"]["status"] == "rejected"
    for name in ["r430_fcf_quality_portfolio_preregistration.json", "r430_fcf_quality_portfolio_2026-11-22.json", "r430_fcf_quality_portfolio_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
