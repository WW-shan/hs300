from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r453_value_quality_portfolio_preregistration.json"
OUTPUT = E / "r453_value_quality_portfolio_2026-11-22.json"


def test_r453_sidecars_and_controls() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "rejected"
    assert payload["decision"]["active_version"] is None
    assert payload["gate"]["checks"]["increment_vs_four_test_positive"] is True
    assert payload["gate"]["checks"]["increment_vs_four_recent_positive"] is False
    assert payload["diagnostics"]["five_x_hard_rejection"] is False


def test_r453_selection_is_train_validation_only() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    assert selected["name"] in {"four_plus_value_quality_10", "four_plus_value_quality_20"}
    assert selected["positions"] in {10, 20}
    assert selected["hold"] in {30, 40}
    for row in payload["grid"]:
        assert row["train"]["stats"]["annual_return"] >= 0.03
        assert row["validation"]["stats"]["annual_return"] >= 0.03
    assert payload["selected"]["recent"]["stats"]["annual_return"] < 0
    assert payload["selected"]["recent_bootstrap"]["total_p05"] < 0
    assert payload["selected"]["test_bootstrap"]["total_p05"] < 0
