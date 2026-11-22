from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data" / "factor_research" / "research"
PREREG = E / "r479_hs300_benchmark_correction_preregistration.json"
OUTPUT = E / "r479_hs300_benchmark_correction_2026-11-22.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r479_freezes_hs300_benchmark() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["round"] == "R479"
    assert payload["benchmark"]["symbol"] == "000300.SH"
    assert payload["superseded_benchmark"]["symbol"] == "000001.SH"
    assert payload["output_policy"]["active_version"] is None
    assert _sha(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r479_outputs_use_hs300_for_both_windows() -> None:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert payload["benchmark_symbol"] == "000300.SH"
    for window in ("test", "recent"):
        item = payload["windows"][window]
        assert item["strategy_total_return"] is not None
        assert item["hs300_total_return"] is not None
        assert item["excess_return"] is not None
        assert item["superseded_shanghai_total_return"] is not None
