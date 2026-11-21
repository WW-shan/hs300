from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r405_has_real_benchmark_columns_and_worst_month() -> None:
    payload = json.loads((E / "r405_tail_attribution_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["monthly"]
    assert any(abs(row["excess"]) > 1e-6 for row in payload["monthly"])
    assert payload["worst_month"]["month"] == "2026-06"
    assert payload["decision"]["active_version"] is None


def test_r406_state_defense_did_not_pass() -> None:
    payload = json.loads((E / "r406_state_drawdown_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert all(row["pass"] is False for row in payload["rows"])


def test_r405_r406_checksums() -> None:
    for name in ("r405_tail_attribution_2026-11-22.json", "r405_tail_attribution_2026-11-22.md", "r406_state_drawdown_2026-11-22.json", "r406_state_drawdown_2026-11-22.md"):
        path = E / name
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
