from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r408_cap_is_negative_and_no_promotion():
    payload = json.loads((E / "r408_industry_cap_backtest_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    base = next(row for row in payload["rows"] if row["cap"] == 0 and row["window"] == "test")
    capped = next(row for row in payload["rows"] if row["cap"] == 2 and row["window"] == "test")
    assert capped["metrics"]["total_return"] < base["metrics"]["total_return"]
    assert capped["metrics"]["mdd"] < base["metrics"]["mdd"]
    for path in (E / "r408_industry_cap_backtest_2026-11-22.json", E / "r408_industry_cap_backtest_2026-11-22.md"):
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
