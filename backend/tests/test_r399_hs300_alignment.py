from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_alignment_uses_real_hs300_and_is_checksum_bound() -> None:
    paths = [
        E / "r399_hs300_alignment_preregistration.json",
        E / "r399_hs300_alignment_2026-11-22.json",
        E / "r399_hs300_alignment_2026-11-22.md",
    ]
    for path in paths:
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    assert payload["index_symbol"] == "000300.SH"
    assert payload["candidates"]
    assert all(row["hs300_total_return"] is not None for row in payload["candidates"])


def test_alignment_does_not_promote_or_hide_recent_underperformance() -> None:
    payload = json.loads((E / "r399_hs300_alignment_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["orders_generated"] is False
    marker = (E / "r399_hs300_alignment_2026-11-22.md").read_text(encoding="utf-8")
    assert "上证指数" in marker
    assert "最近一年" in marker or "2025-05-06" in marker
    assert any(row["hs300_excess"] < 0 for row in payload["candidates"])
