from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_r264_r131_concentration.py"


def _load():
    spec = importlib.util.spec_from_file_location("r264_concentration_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_weight_at_uses_latest_record_not_future() -> None:
    mod = _load()
    records = [
        {"date": date(2025, 1, 1), "weight_a": 0.5},
        {"date": date(2025, 2, 1), "weight_a": 0.7},
        {"date": date(2025, 3, 1), "weight_a": 0.3},
    ]

    assert mod._weight_at(records, date(2025, 1, 15), "weight_a") == 0.5
    assert mod._weight_at(records, date(2025, 2, 1), "weight_a") == 0.7
    assert mod._weight_at(records, date(2025, 4, 1), "weight_a") == 0.3


def test_parse_day_handles_string_and_date() -> None:
    mod = _load()

    assert mod._parse_day("2025-01-02") == date(2025, 1, 2)
    assert mod._parse_day(date(2025, 1, 3)) == date(2025, 1, 3)
