from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r410_historical_membership_is_small_and_no_promotion():
    payload = json.loads((E / "r410_historical_membership_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    test_return = payload["reports"]["test"]["metrics"]["total_return"]
    recent_return = payload["reports"]["recent"]["metrics"]["total_return"]
    assert 0.20 < test_return < 0.23
    assert 0.10 < recent_return < 0.14
    for name in ("r410_historical_membership_2026-11-22.json", "r410_historical_membership_2026-11-22.md", "r410_historical_membership_preregistration.json"):
        path = E / name
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
