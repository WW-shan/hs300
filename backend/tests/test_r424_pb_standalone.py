from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r424_pb_standalone_preregistration.json"


def test_r424_freezes_low_pb_direction_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R424"
    assert payload["members"] == ["pb_latest"]
    assert payload["directions"]["pb_latest"] == "low"
    assert payload["execution"]["no_leverage"] is True
    assert len(payload["grid"]["max_positions"]) == 2


def test_r424_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r424_standalone_pb_rejected_on_test_tail() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r424_pb_standalone_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected"]["test_2x"]["stats"]["annual_return"] < 0
    assert payload["selected"]["test_2x"]["stats"]["max_drawdown"] < -0.20
    assert payload["decision"]["status"] == "rejected"
    for name in ["r424_pb_standalone_preregistration.json", "r424_pb_standalone_2026-11-22.json", "r424_pb_standalone_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
