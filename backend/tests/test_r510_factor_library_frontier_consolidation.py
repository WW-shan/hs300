from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
OUTPUT = E / "r510_factor_library_frontier_consolidation_2026-11-22.json"
RESULT = E / "r510_factor_library_frontier_consolidation_2026-11-22.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r510_consolidation_is_current() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["round"] == "R510"
    assert payload["frontier"]["total_catalog"] == 235
    assert payload["frontier"]["statistical_pass"] == 11
    assert payload["frontier"]["r508_eligible"] == 0
    assert payload["frontier"]["r509_eligible"] == 0
    assert payload["decision"]["active_version"] is None


def test_r510_sidecars() -> None:
    for path in (OUTPUT, RESULT):
        assert (
            _sha(path)
            == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        )
