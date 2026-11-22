from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r471_frontier_reconciliation_preregistration.json"
OUTPUT = E / "r471_frontier_reconciliation_2026-11-22.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r471_is_read_only_and_sidecar_bound() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R471"
    assert payload["rules"]["read_only"] is True
    assert payload["rules"]["selection_allowed"] is False
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r471_frontier_has_no_active_strategy() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert _sha(OUTPUT) == OUTPUT.with_name(OUTPUT.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["frontier"]["active_version"] is None
    assert payload["frontier"]["no_leverage_final_strategy"] is None
    assert payload["decision"]["status"] == "frontier_reconciliation_complete_no_promotion"
    assert len(payload["frontier"]["current_near_candidates"]) == 4
    assert all(row["status"].startswith("rejected") for row in payload["frontier"]["current_near_candidates"])
