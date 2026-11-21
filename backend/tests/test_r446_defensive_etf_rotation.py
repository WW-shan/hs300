from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r446_defensive_etf_rotation_preregistration.json"


def test_r446_freezes_six_etfs_and_five_rules() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R446"
    assert len(payload["universe"]) == 6
    assert len(payload["rule_families"]) == 5
    assert payload["execution"]["no_leverage"] is True


def test_r446_sidecar() -> None:
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == PREREG.with_name(
        PREREG.name + ".sha256"
    ).read_text().split()[0]


def test_r446_all_rules_rejected_on_tail() -> None:
    output = E / "r446_defensive_etf_rotation_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "rejected"
    assert payload["gate"]["checks"]["test_total_p05_positive"] is False
    assert payload["gate"]["checks"]["recent_total_p05_positive"] is False
    for name in [
        "r446_defensive_etf_rotation_preregistration.json",
        "r446_defensive_etf_rotation_2026-11-22.json",
        "r446_defensive_etf_rotation_2026-11-22.md",
    ]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(
            path.name + ".sha256"
        ).read_text().split()[0]
