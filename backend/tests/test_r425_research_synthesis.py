from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r425_research_synthesis_preregistration.json"


def test_r425_synthesis_is_read_only_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R425"
    assert payload["output_policy"]["active_version"] is None
    assert payload["rules"]["must_distinguish_statistical_ic_from_portfolio_tail"] is True
    assert len(payload["inputs"]) >= 6


def test_r425_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r425_synthesis_does_not_recommend_rejected_candidate() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r425_research_synthesis_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["closest_but_not_recommended"] == "current_8f"
    assert payload["decision"]["status"] == "no_final_no_leverage_strategy_passed_tail_and_recent"
    assert payload["financial_portfolio"]["r424_status"] == "rejected"
    assert payload["financial_portfolio"]["r423_status"] == "rejected"
    for name in ["r425_research_synthesis_preregistration.json", "r425_research_synthesis_2026-11-22.json", "r425_research_synthesis_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
