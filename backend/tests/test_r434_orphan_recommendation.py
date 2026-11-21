from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "data" / "factor_research" / "research" / "r434_orphan_recommendation_preregistration.json"


def test_r434_only_tests_amihud_orphan() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R434"
    assert len(payload["variants"]) == 1
    assert payload["variants"][0]["add"] == ["amihud_20d"]
    assert len(payload["excluded_rejected_recommendations"]) == 3
    assert payload["execution"]["no_leverage"] is True


def test_r434_sidecar() -> None:
    sidecar = PREREG.with_name(PREREG.name + ".sha256")
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar.read_text().split()[0]


def test_r434_rejects_amihud_swap() -> None:
    output = ROOT / "data" / "factor_research" / "research" / "r434_orphan_recommendation_2026-11-22.json"
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["selected_variant"] == "baseline"
    assert payload["decision"]["status"] == "rejected"
    for name in ["r434_orphan_recommendation_preregistration.json", "r434_orphan_recommendation_2026-11-22.json", "r434_orphan_recommendation_2026-11-22.md"]:
        path = ROOT / "data" / "factor_research" / "research" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
