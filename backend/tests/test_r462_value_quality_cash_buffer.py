from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r462_value_quality_cash_buffer_preregistration.json"
OUTPUT = E / "r462_value_quality_cash_buffer_2026-11-22.json"


def test_r462_sidecars_and_no_leverage() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "rejected"
    assert payload["decision"]["active_version"] is None
    assert payload["diagnostics"]["no_leverage"] is True


def test_r462_exposure_reduces_tail_but_not_recent_positive() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    selected = payload["selected"]
    assert selected["exposure"] == 1.0
    assert selected["test_bootstrap"]["total_p05"] < 0
    assert selected["recent_bootstrap"]["total_p05"] < 0
    rows = {(row["exposure"], row["positions"], row["hold"]): row for row in payload["rows"]}
    assert rows[(0.4, 10, 30)]["validation"]["stats"]["annual_return"] < rows[(1.0, 10, 30)]["validation"]["stats"]["annual_return"]
