from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data/factor_research/research/r524_cross_generation_evidence_reconciliation_preregistration.json"
OUTPUT = ROOT / "data/factor_research/research/r524_cross_generation_evidence_reconciliation_2026-11-23.json"
RESULT = ROOT / "data/factor_research/research/r524_cross_generation_evidence_reconciliation_2026-11-23.md"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r524_preregistration_is_frozen_and_forbids_direct_ranking() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R524"
    assert payload["comparability"]["direct_return_comparison_allowed"] is False
    assert payload["next_comparison"]["no_direct_winner_claim_before_recompute"] is True
    assert payload["output_policy"]["active_version"] is None
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text().split()[0]


def test_r524_sources_exist_and_have_sidecars() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    for path in payload["source_rounds"].values():
        source = ROOT / path
        assert source.exists(), source
        assert source.with_name(source.name + ".sha256").exists(), source


def test_r524_output_records_the_data_vintage_mismatch_when_present() -> None:
    if not OUTPUT.exists() or not RESULT.exists():
        return
    for path in (OUTPUT, RESULT):
        assert _sha(path) == path.with_name(path.name + ".sha256").read_text().split()[0]
    result = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert result["comparability"]["direct_return_comparison_allowed"] is False
    assert result["decision"]["active_version"] is None
    assert result["decision"]["orders_generated"] is False
    assert result["decision"]["production_enabled"] is False
