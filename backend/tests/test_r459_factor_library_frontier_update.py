from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r459_factor_library_frontier_update_preregistration.json"
OUTPUT = E / "r459_factor_library_frontier_update_2026-11-22.json"


def test_r459_sidecars_and_no_promotion() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "frontier_update_complete_no_promotion"
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False


def test_r459_preserves_statistical_but_rejected_distinction() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    value = payload["frontier"]["new_research"]["value_quality"]
    assert value["best_neutral_ic_60d"] > 0
    assert value["portfolio_status"] == "rejected"
    assert value["portfolio_test_p05"] < 0
    assert value["compact_status"] == "rejected"
    assert value["defensive_mix_status"] == "rejected"
    emotion = payload["frontier"]["new_research"]["emotion_condition"]
    assert emotion["passing_cells"] == [{"emotion": "market_profit_score", "sleeve": "idio_vol_40_low", "horizon": "20"}]
