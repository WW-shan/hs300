from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r414_statistical_pass_portfolio_preregistration.json"


def test_r414_freezes_r391_pass_set_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R414"
    assert len(payload["candidates"]) == 11
    assert payload["selection_rule"]["max_members"] == 8
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["max_exposure_pct"] == 1.0
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r414_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r414_rejects_when_training_gate_fails() -> None:
    output = E / "r414_statistical_pass_portfolio_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert len(payload["selected_members"]) == 8
    assert "amihud_20d" in payload["data_unavailable"]
    if not payload["gate"]["checks"]["train_annual_min"]:
        assert payload["decision"]["status"] == "rejected"
    for name in [
        "r414_statistical_pass_portfolio_preregistration.json",
        "r414_statistical_pass_portfolio_2026-11-22.json",
        "r414_statistical_pass_portfolio_2026-11-22.md",
    ]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
