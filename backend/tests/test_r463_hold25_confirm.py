from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r463_hold25_confirm_preregistration.json"
OUTPUT = E / "r463_hold25_confirm_2026-11-22.json"


def test_r463_sidecars_and_no_leverage() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["execution"]["no_leverage"] is True
    assert payload["execution"]["max_weight_sum"] == 1.0
    assert payload["execution"]["cost_diagnostic"] == "5x"


def test_r463_selects_25_day_but_rejects_recent_tail() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    assert selected["positions"] == 10
    assert selected["hold"] == 25
    assert selected["test"]["stats"]["annual_return"] > 0.20
    assert selected["test_bootstrap"]["total_p05"] > 0
    assert selected["recent"]["stats"]["annual_return"] > 0
    assert selected["recent_bootstrap"]["total_p05"] < 0
    assert payload["decision"]["status"] == "rejected"
    assert payload["decision"]["active_version"] is None
