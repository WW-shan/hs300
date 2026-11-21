from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/research"
PREREG = E / "r403_core_beta_preregistration.json"
REPORT = E / "r403_core_beta_2026-11-22.json"
NEIGHBORHOOD = E / "r403_neighborhood_2026-11-22.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_r403_frozen_and_no_leverage() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    assert payload["core_candidate"]["id"] == 4
    assert payload["beta"]["symbol"] == "000300.SH"
    assert all(core + beta == 1.0 for core, beta in zip(payload["weight_grid"]["core"], payload["weight_grid"]["beta"], strict=True))
    assert payload["output_policy"]["active_version"] is None
    assert sha256(PREREG) == PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r403_reports_real_recent_hs300() -> None:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    assert payload["decision"]["status"] == "core_beta_complete_no_promotion"
    assert payload["decision"]["active_version"] is None
    assert all(row["recent_hs300_return"] == payload["rows"][0]["recent_hs300_return"] for row in payload["rows"])
    assert all(row["recent_excess"] > 0 for row in payload["rows"])
    for path in (REPORT, E / "r403_core_beta_2026-11-22.md"):
        assert sha256(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]


def test_r403_neighborhood_is_checksum_bound_and_stable() -> None:
    payload = json.loads(NEIGHBORHOOD.read_text(encoding="utf-8"))
    assert payload["decision"]["active_version"] is None
    assert all(item["stable"] for item in payload["audit"])
    for path in (NEIGHBORHOOD, E / "r403_neighborhood_2026-11-22.md", E / "r403_neighborhood_preregistration.json"):
        assert sha256(path) == path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
