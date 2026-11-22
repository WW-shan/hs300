from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r523_frontier_audit_preregistration.json"
OUTPUT = E / "r523_frontier_audit_2026-11-23.json"
RESULT = E / "r523_frontier_audit_2026-11-23.md"
RUNNER = ROOT / "scripts" / "research_r523_frontier_audit.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r523_preregistration_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R523"
    assert "r522_r506_fresh_start_year_audit_2026-11-23.json" in payload["sources"]
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]


def test_r523_outputs_are_consolidation_only() -> None:
    for path in (OUTPUT, RESULT):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["frontier"]["industry_momentum_passed"] is False
    assert payload["frontier"]["r506_fresh_start_positive"] == "5/5"
    assert payload["gate"]["checks"]["active_version_is_null"] is True
    assert payload["decision"]["active_version"] is None


def test_r523_source_hashes_are_recorded() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert set(payload["source_hashes"]) == {
        "r510_factor_library_frontier_consolidation_2026-11-22.json",
        "r516_frontier_update_2026-11-22.json",
        "r520_industry_index_feasibility_2026-11-22.json",
        "r521_industry_index_momentum_2026-11-23.json",
        "r522_r506_fresh_start_year_audit_2026-11-23.json",
    }
    assert all(value for value in payload["source_hashes"].values())
