from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r407_reports_bank_concentration_and_no_promotion():
    payload = json.loads((E / "r407_industry_cap_2026-11-22.json").read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert payload["decision"]["status"] == "industry_cap_diagnostic_complete_no_promotion"
    assert payload["reports"]["test"]["industry_counts"]["银行"] >= 20
    assert "cannot implement" in payload["decision"]["reason"] or "no selection-time industry cap" in payload["decision"]["reason"]


def test_r407_checksums():
    for name in ("r407_industry_cap_2026-11-22.json", "r407_industry_cap_2026-11-22.md", "r407_industry_cap_preregistration.json"):
        path = E / name
        assert sha(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
