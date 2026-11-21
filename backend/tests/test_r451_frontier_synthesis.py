from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r451_frontier_synthesis_preregistration.json"
OUTPUT = E / "r451_frontier_synthesis_2026-11-22.json"


def test_r451_sidecars_and_no_promotion() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "no_final_no_leverage_strategy"
    assert payload["decision"]["active_version"] is None
    assert payload["frontier"]["current_best_hs300"]["status"] == "rejected_on_negative_tail"
    assert payload["frontier"]["five_factor"]["status"] == "rejected"
    assert payload["frontier"]["defensive_etf"]["status"] == "rejected_on_negative_tail_and_recent"


def test_r451_does_not_confuse_best_point_with_selection() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    current = payload["frontier"]["current_best_hs300"]
    assert current["selection"]["max_positions"] == 10
    assert current["selection"]["max_hold_days"] == 30
    assert current["best_point_estimate_not_selection"]["max_hold_days"] == 40
    assert current["best_point_estimate_not_selection"]["test_annual"] > current["selection"]["test_annual"]
