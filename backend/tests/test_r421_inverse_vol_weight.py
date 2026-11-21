from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r421_inverse_vol_weight_preregistration.json"


def test_r421_freezes_inverse_vol_rule_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R421"
    assert payload["weight_rule"]["type"] == "inverse_volatility"
    assert payload["weight_rule"]["maximum_weight"] == 0.30
    assert payload["execution"]["no_leverage"] is True
    assert payload["gates"]["test_total_p05_not_worse_than_equal_weight"] is True


def test_r421_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r421_invalidates_when_inverse_vol_does_not_vary() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r421_inverse_vol_weight_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["max_weight_delta"] is not None
    if payload["max_weight_delta"] < 0.005:
        assert payload["gate"]["checks"]["weight_variation_exists"] is False
        assert payload["gate"]["pass"] is False
    for name in ["r421_inverse_vol_weight_preregistration.json", "r421_inverse_vol_weight_2026-11-22.json", "r421_inverse_vol_weight_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
