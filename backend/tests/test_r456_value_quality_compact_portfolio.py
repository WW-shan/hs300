from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r456_value_quality_compact_portfolio_preregistration.json"
OUTPUT = E / "r456_value_quality_compact_portfolio_2026-11-22.json"


def test_r456_sidecars_and_no_promotion() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "rejected"
    assert payload["decision"]["active_version"] is None
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False


def test_r456_compact_has_positive_train_validation_but_weak_recent() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    assert selected["train"]["stats"]["annual_return"] >= 0.03
    assert selected["validation"]["stats"]["annual_return"] >= 0.03
    assert selected["recent"]["stats"]["annual_return"] >= 0.0
    assert selected["test"]["stats"]["sharpe"] < 0.45
    assert payload["increment_vs_four"]["recent_annual"] < 0
