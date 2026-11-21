from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r449_five_factor_composite_preregistration.json"
OUTPUT = E / "r449_five_factor_composite_2026-11-22.json"


def test_r449_sidecars_and_no_leverage() -> None:
    for path in [PREREG, OUTPUT, OUTPUT.with_suffix(".md")]:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["controls"]["no_leverage"] is True
    assert payload["execution"]["five_x_hard_rejection"] is False


def test_r449_five_factor_rejected_on_tail_and_neutrality() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["gate"]["pass"] is False
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["checks"]["five_factor_beats_four_factor"] is True
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False
    assert payload["gate"]["checks"]["industry_size_neutral_signal"] is False
    assert payload["decision"]["active_version"] is None
    assert payload["diagnostics"]["five_x_warning_only"] is True
