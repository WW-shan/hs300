from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r422_financial_multihorizon_neutral_preregistration.json"


def test_r422_freezes_three_horizons_and_neutralization() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R422"
    assert payload["horizons"] == [5, 20, 60]
    assert payload["neutralization"]["mode"] == "industry+size"
    assert payload["gates"]["yearly_sign_consistency_min"] == 0.80
    assert payload["output_policy"]["active_version"] is None


def test_r422_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r422_pb_and_ocf_debt_pass_all_horizons() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r422_financial_multihorizon_neutral_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["rows"]["pb_latest"]["passing_horizons"] == [5, 20, 60]
    assert payload["rows"]["ocf_debt"]["passing_horizons"] == [5, 20, 60]
    assert payload["rows"]["pb_latest"]["best_horizon"] == 60
    for name in ["r422_financial_multihorizon_neutral_preregistration.json", "r422_financial_multihorizon_neutral_2026-11-22.json", "r422_financial_multihorizon_neutral_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
