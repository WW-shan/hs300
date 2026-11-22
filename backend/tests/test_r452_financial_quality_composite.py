from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r452_financial_quality_composite_preregistration.json"
OUTPUT = E / "r452_financial_quality_composite_2026-11-22.json"


def test_r452_sidecars_and_no_promotion() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
    assert payload["decision"]["admitted_candidates"] == ["value_quality", "value_quality_safety"]


def test_r452_admission_requires_horizon_and_increment() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    quality = payload["candidates"]["value_quality"]
    assert all(quality["horizons"][str(h)]["gate"]["pass"] for h in (5, 20, 60))
    assert quality["horizons"]["60"]["neutral_ic"]["full"]["directional_ic_mean"] > 0
    assert quality["horizons"]["60"]["incremental"]["nw_p"] <= 0.10
    value_cash = payload["candidates"]["value_cash"]
    assert not value_cash["horizons"]["60"]["gate"]["pass"]
    assert value_cash["horizons"]["60"]["gate"]["checks"]["incremental_nw_p_le"] is False
    safety = payload["candidates"]["value_quality_safety"]
    assert not safety["horizons"]["60"]["gate"]["pass"]
    assert safety["horizons"]["60"]["gate"]["checks"]["incremental_yearly_sign_consistency_min"] is False
