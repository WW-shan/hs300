from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/factor_research/research"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r401_invalid_composition_is_disclosed() -> None:
    report = json.loads((EVIDENCE / "r401_regime_beta_2026-11-22.json").read_text(encoding="utf-8"))
    assert report["decision"]["status"] == "regime_composition_invalid_no_promotion"
    assert report["decision"]["active_version"] is None
    assert "did not produce distinct beta-on curves" in report["decision"]["reason"]
    for name in ("r401_regime_beta_2026-11-22.json", "r401_regime_beta_2026-11-22.md"):
        path = EVIDENCE / name
        assert sha256(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
