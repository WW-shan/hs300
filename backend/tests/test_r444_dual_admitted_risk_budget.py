from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r444_dual_admitted_risk_budget_preregistration.json"


def test_r444_freezes_six_risk_budget_overlays() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R444"
    assert len(payload["overlays"]) == 6
    assert payload["base_execution"]["no_leverage"] is True
    assert payload["gates"]["test_total_p05_positive"] is True
    assert payload["gates"]["recent_total_p05_positive"] is True


def test_r444_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r444_no_overlay_beats_baseline() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r444_dual_admitted_risk_budget_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_overlay"] == "baseline"
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False
    for name in ["r444_dual_admitted_risk_budget_preregistration.json", "r444_dual_admitted_risk_budget_2026-11-22.json", "r444_dual_admitted_risk_budget_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
