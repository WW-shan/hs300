from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[2] / "data" / "factor_research" / "research"


def test_r411_discloses_blocker() -> None:
    payload = json.loads(
        (EVIDENCE / "r411_forward_state_2026-11-22.json").read_text(encoding="utf-8")
    )
    assert payload["status"] == "blocked"
    assert payload["target_positions"] == []
    assert payload["decision"]["active_version"] is None
    for name in [
        "r411_forward_state_2026-11-22.json",
        "r411_forward_state_2026-11-22.md",
        "r411_forward_state_preregistration.json",
    ]:
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar.read_text(
            encoding="ascii"
        ).split()[0]
