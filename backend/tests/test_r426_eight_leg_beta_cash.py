from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r426_eight_leg_beta_cash_preregistration.json"


def test_r426_freezes_beta_cash_grid_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R426"
    assert payload["grid"]["beta_weight"] == [0.0, 0.1, 0.2, 0.3]
    assert payload["grid"]["cash_weight"] == [0.0, 0.1]
    assert payload["output_policy"]["active_version"] is None


def test_r426_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r426_no_beta_cash_sleeve_makes_tail_positive() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r426_eight_leg_beta_cash_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["eligible_count"] == 0
    assert payload["selected"]["test_bootstrap"]["total_p05"] < 0
    assert payload["selected"]["recent_bootstrap"]["total_p05"] < 0
    for name in ["r426_eight_leg_beta_cash_preregistration.json", "r426_eight_leg_beta_cash_2026-11-22.json", "r426_eight_leg_beta_cash_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
