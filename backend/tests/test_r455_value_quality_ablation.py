from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r455_value_quality_ablation_preregistration.json"
OUTPUT = E / "r455_value_quality_ablation_2026-11-22.json"


def test_r455_sidecars_and_ablation_order() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert rows["pb_ocf"]["horizons"]["60"]["full"]["directional_ic_mean"] > rows["pb_only"]["horizons"]["60"]["full"]["directional_ic_mean"]
    assert rows["debt_only"]["horizons"]["60"]["gate"]["pass"] is False
    assert rows["pb_ocf"]["horizons"]["60"]["gate"]["pass"] is True
    assert payload["decision"]["active_version"] is None
