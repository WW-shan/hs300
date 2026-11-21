from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/factor_research/research"
RUNNER = ROOT / "scripts/research_r397_factor_subset_mc.py"
PREREG = EVIDENCE / "r397_factor_subset_preregistration.json"
REPORT = EVIDENCE / "r397_factor_subset_mc_2026-11-22.json"
CONFIRM_REPORT = EVIDENCE / "r397_factor_subset_confirm_2026-11-22.json"


def _module():
    spec = importlib.util.spec_from_file_location("r397_subset", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preregistration_and_grid_are_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert payload["round"] == "R397"
    assert _sha256(PREREG) == sidecar
    module = _module()
    assert len(module.combinations(payload, payload["factor_ids"])) == 1962


def test_reports_record_holding_bug_not_hidden() -> None:
    for path in (REPORT, CONFIRM_REPORT):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert payload["holding_days_effective"] is False
        assert payload["decision"]["active_version"] is None
        assert _sha256(path) == sidecar
    markdown = (EVIDENCE / "r397_factor_subset_confirm_2026-11-22.md").read_text(encoding="utf-8")
    assert "口径警告" in markdown


def test_confirmation_uses_platform_candidates_only() -> None:
    payload = json.loads(CONFIRM_REPORT.read_text(encoding="utf-8"))
    assert payload["candidate_count"] == 33
    assert all(row["train_platform_pass_count"] == 9 for row in payload["candidates"])
    assert payload["confirmation_pass_count"] == 15
