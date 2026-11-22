from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r517_industry_history_feasibility_preregistration.json"
OUTPUT = E / "r517_industry_history_feasibility_2026-11-22.json"
RESULT = E / "r517_industry_history_feasibility_2026-11-22.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r517_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R517"
    assert payload["start"] == "2018-01-02"
    assert payload["requirements"]["minimum_long_history_days"] == 1000
    assert payload["requirements"]["minimum_industry_proxies"] == 8
    assert (
        hashlib.sha256(PREREG.read_bytes()).hexdigest()
        == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    )


def test_r517_rejects_insufficient_industry_history() -> None:
    for path in (OUTPUT, RESULT):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["etf"]["long_history_count"] == 6
    assert payload["index"]["long_history_count"] == 5
    assert payload["etf"]["distinct_industry_labels"] == []
    assert payload["index"]["distinct_industry_labels"] == []
    assert payload["gate"]["pass"] is False
    assert payload["decision"]["active_version"] is None
