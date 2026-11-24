from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_grandfather_manifest_matches_frozen_runner_hashes() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "data/factor_research/research/research_contract_grandfather_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == len(payload["rows"])
    assert payload["rows"]
    for row in payload["rows"]:
        runner = root / row["path"]
        assert runner.is_file(), row["path"]
        assert hashlib.sha256(runner.read_bytes()).hexdigest() == row["sha256"], row["path"]
        assert row["disposition"] == "legacy_grandfathered"
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()
