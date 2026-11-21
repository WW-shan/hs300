from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r409_attribution_is_negative_and_no_promotion():
    payload = json.loads((E / "r409_leg_attribution_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["rows"]
    assert payload["stress_3x"]["test_total_return"] > 0
    assert payload["stress_3x"]["test_mdd"] < 0
    for path in (E / "r409_leg_attribution_2026-11-22.json", E / "r409_leg_attribution_2026-11-22.md"):
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
