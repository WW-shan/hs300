from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
REPORT = E / "r403v2_core_beta_2026-11-22.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r403v2_curves_are_weight_distinct_and_point_consistent() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    for window in ("test", "recent"):
        assert payload["curves"]["0.7"][window] != payload["curves"]["0.8"][window]
        assert payload["curves"]["0.8"][window] != payload["curves"]["0.9"][window]
    for row in payload["rows"]:
        curve = payload["curves"][str(row["core_weight"])]
        for window in ("train", "validation", "test", "recent"):
            values = [item["value"] for item in curve[window]]
            point = values[-1] / values[0] - 1.0
            assert abs(point - row[f"{window}_2x"]["total_return"]) < 1e-12
    assert payload["decision"]["active_version"] is None


def test_r403v2_checksum() -> None:
    for path in (REPORT, E / "r403v2_core_beta_2026-11-22.md"):
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
