from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r460_emotion_macro_t1_condition_preregistration.json"
OUTPUT = E / "r460_emotion_macro_t1_condition_2026-11-22.json"


def test_r460_sidecars_and_supersedes_r458() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["supersedes_round"] == "R458"
    assert payload["conditioning"]["state_effective"] == "next trading day after month-end"
    output = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert output["decision"]["active_version"] is None
    assert output["decision"]["orders_generated"] is False


def test_r460_no_condition_cell_passes_after_t1_fix() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["pass_rows"] == []
    assert all(row["gate"]["checks"]["bootstrap_ci_excludes_zero"] is False for rows in payload["rows"].values() for horizons in rows.values() for row in horizons.values())
