from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r454_value_quality_defensive_mix_preregistration.json"
OUTPUT = E / "r454_value_quality_defensive_mix_2026-11-22.json"


def test_r454_sidecars_and_no_leverage() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["max_weight_sum"] == 1.0
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r454_rejects_on_drawdown_and_tail() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "rejected"
    assert payload["decision"]["active_version"] is None
    assert payload["gate"]["checks"]["test_mdd_min"] is False
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False
    assert payload["selected_mix"]["equity_weight"] + payload["selected_mix"]["defensive_weight"] + payload["selected_mix"]["cash_weight"] == 1.0
