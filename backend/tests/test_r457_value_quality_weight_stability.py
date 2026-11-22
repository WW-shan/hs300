from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r457_value_quality_weight_stability_preregistration.json"
OUTPUT = E / "r457_value_quality_weight_stability_2026-11-22.json"


def test_r457_sidecars_and_weight_grid() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["gate"]["pass"] is True
    assert payload["bootstrap"]["ci_low"] > 0
    assert payload["neighbor_count"] >= 1
    assert len(payload["variants"]) == 231
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
