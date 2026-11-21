from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r428_pb_ocf_lowvol_preregistration.json"


def test_r428_freezes_three_legs_and_directions() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R428"
    assert payload["members"] == ["pb_latest", "ocf_debt", "ext_alt_hs300_idio_vol_40d_low"]
    assert payload["directions"]["pb_latest"] == "low"
    assert payload["execution"]["no_leverage"] is True


def test_r428_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r428_combination_rejected_on_negative_test_and_tail() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r428_pb_ocf_lowvol_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected"]["test_2x"]["stats"]["annual_return"] < 0
    assert payload["selected"]["test_bootstrap"]["total_p05"] < 0
    assert payload["decision"]["status"] == "rejected"
    for name in ["r428_pb_ocf_lowvol_preregistration.json", "r428_pb_ocf_lowvol_2026-11-22.json", "r428_pb_ocf_lowvol_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
