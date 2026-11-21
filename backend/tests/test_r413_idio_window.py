from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r413_idio_window_preregistration.json"


def test_r413_freezes_only_idio_window_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R413"
    assert payload["baseline_members"][0] == "ext_alt_hs300_idio_vol_40d_low"
    assert payload["variant_members"][0] == "ext_alt_hs300_idio_vol_20d_low"
    assert payload["baseline_members"][1:] == payload["variant_members"][1:]
    assert payload["execution"]["max_exposure_pct"] == 1.0
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r413_preregistration_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r413_output_rejects_when_training_delta_is_negative() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r413_idio_window_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] in {"watch_candidate", "rejected"}
    assert payload["decision"]["active_version"] is None
    assert payload["deltas"]["train"]["annual_delta"] is not None
    if payload["deltas"]["train"]["annual_delta"] <= 0:
        assert payload["decision"]["status"] == "rejected"
    for name in ["r413_idio_window_2026-11-22.json", "r413_idio_window_2026-11-22.md", "r413_idio_window_preregistration.json"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
