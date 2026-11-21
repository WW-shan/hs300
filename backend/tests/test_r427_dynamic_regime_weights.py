from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r427_dynamic_regime_weights_preregistration.json"


def test_r427_freezes_regime_alignment_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R427"
    assert payload["regime_alignment"].startswith("T-1 state assigned to next trading date")
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False
    assert payload["gates"]["weight_variation_exists"] is True


def test_r427_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r427_dynamic_weights_rejected_when_test_worse_than_static() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r427_dynamic_regime_weights_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["max_weight_variation"] > 0.005
    assert payload["dynamic"]["test_2x"]["stats"]["annual_return"] < payload["static"]["test_2x"]["stats"]["annual_return"]
    assert payload["gate"]["checks"]["test_total_p05_not_worse"] is False
    for name in ["r427_dynamic_regime_weights_preregistration.json", "r427_dynamic_regime_weights_2026-11-22.json", "r427_dynamic_regime_weights_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
