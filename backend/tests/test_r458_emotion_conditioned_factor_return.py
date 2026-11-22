from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r458_emotion_conditioned_factor_return_preregistration.json"
OUTPUT = E / "r458_emotion_conditioned_factor_return_2026-11-22.json"


def test_r458_sidecars_and_no_promotion() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False


def test_r458_only_one_condition_cell_passes_all_gates() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    passes = [
        (emotion, sleeve, horizon)
        for emotion, by_sleeve in payload["states"].items()
        for sleeve, by_horizon in by_sleeve.items()
        for horizon, row in by_horizon.items()
        if row["gate"]["pass"]
    ]
    assert passes == [("market_profit_score", "idio_vol_40_low", "20")]
    row = payload["states"]["market_emotion_score"]["idio_vol_40_low"]["20"]
    assert row["gate"]["pass"] is False
    assert row["high_minus_low"] > 0
