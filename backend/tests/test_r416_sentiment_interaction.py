from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r416_sentiment_interaction_preregistration.json"


def test_r416_freezes_three_new_sentiment_factors_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R416"
    assert len(payload["candidates"]) == 3
    assert payload["output_policy"]["active_version"] is None
    assert payload["known_limits"][-1].startswith("No leverage")


def test_r416_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r416_new_panic_factor_gate_is_conditional_and_research_only() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r416_sentiment_interaction_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    row = payload["candidates"]["new_panic_reversal_5d"]
    assert row["status"] in {"statistical_pass", "statistical_rejected"}
    assert row["gate"]["checks"]["minimum_n_dates"]
    assert payload["decision"]["active_version"] is None
    for name in ["r416_sentiment_interaction_preregistration.json", "r416_sentiment_interaction_2026-11-22.json", "r416_sentiment_interaction_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
