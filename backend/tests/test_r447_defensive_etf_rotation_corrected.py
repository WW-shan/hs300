from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r447_defensive_etf_rotation_corrected_preregistration.json"


def test_r447_supersedes_r446() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R447"
    assert payload["supersedes_round"] == "R446"
    assert payload["execution"]["no_leverage"] is True


def test_r447_sidecar() -> None:
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == PREREG.with_name(
        PREREG.name + ".sha256"
    ).read_text().split()[0]


def test_r447_corrected_rotation_rejected_on_tail() -> None:
    output = E / "r447_defensive_etf_rotation_corrected_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False
    for name in [
        "r447_defensive_etf_rotation_corrected_preregistration.json",
        "r447_defensive_etf_rotation_corrected_2026-11-22.json",
        "r447_defensive_etf_rotation_corrected_2026-11-22.md",
    ]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
