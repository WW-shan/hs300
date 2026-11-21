from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r415_defensive_leg_swap_preregistration.json"


def test_r415_limits_swaps_and_keeps_eight_leg_cap() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R415"
    assert len(payload["baseline_members"]) == 8
    assert payload["selection_rule"]["max_swaps"] == 2
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r415_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r415_negative_selection_is_explicit() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r415_defensive_leg_swap_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_variant"] in payload["variants"]
    if payload["selected_variant"] == "baseline":
        assert payload["decision"]["status"] == "rejected"
    for name in ["r415_defensive_leg_swap_preregistration.json", "r415_defensive_leg_swap_2026-11-22.json", "r415_defensive_leg_swap_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
