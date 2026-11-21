from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"


def test_r440_volvol_is_not_admitted() -> None:
    payload = json.loads((E / "r440_volvol_research_admission_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["admitted"] is False
    assert payload["passing_horizons"] == []
    assert payload["decision"]["active_version"] is None


def test_r440_sidecars() -> None:
    for name in ["r440_volvol_research_admission_preregistration.json", "r440_volvol_research_admission_2026-11-22.json", "r440_volvol_research_admission_2026-11-22.md"]:
        path = E / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.with_name(path.name + ".sha256").read_text().split()[0]
