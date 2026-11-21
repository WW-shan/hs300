from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r443_dual_admitted_tail_control_preregistration.json"


def test_r443_freezes_six_overlays_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R443"
    assert len(payload["overlays"]) == 6
    assert payload["base_execution"]["no_leverage"] is True
    assert payload["gates"]["test_total_p05_positive"] is True
    assert payload["gates"]["recent_total_p05_positive"] is True


def test_r443_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r443_stop_loss_rejected_on_test() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r443_dual_admitted_tail_control_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "rejected"
    assert payload["selected_overlay"] == "stop_loss_10"
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["test_annual_min"] is False
    for name in ["r443_dual_admitted_tail_control_preregistration.json", "r443_dual_admitted_tail_control_2026-11-22.json", "r443_dual_admitted_tail_control_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
