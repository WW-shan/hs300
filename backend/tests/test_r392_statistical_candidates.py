from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r392_statistical_candidates.py"
PREREG = EVIDENCE / "r392_statistical_candidates_preregistration.json"
R391 = EVIDENCE / "r391_factor_library_statistical.json"
REPORT = EVIDENCE / "r392_statistical_candidates.json"
RESULT = EVIDENCE / "r392_statistical_candidates_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r392_statistical_candidates", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_is_frozen_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R392"
    assert payload["candidate_source"] == "statistical_pass_new_to_portfolio_base"
    assert payload["exclude_candidates_already_in_portfolio_base"] is True
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["output_policy"]["status"] == "portfolio_slot_screen_complete_no_promotion"
    assert payload["output_policy"]["active_version"] is None


def test_candidates_are_exactly_r391_statistical_pass() -> None:
    module = _module()
    r391 = json.loads(R391.read_text(encoding="utf-8"))
    base, _ = module._core_scoring(module._portfolio_module())
    expected = sorted(k for k, v in r391["candidates"].items() if v["status"] == "statistical_pass" and k not in base)
    assert module.candidate_ids() == expected
    assert len(expected) == 7
    assert module.excluded_existing_base_candidates()


def test_grid_is_396_points_and_bounded() -> None:
    module = _module()
    points = module.sample_grid()
    assert len(points) == 7 * 8
    assert len({(p["candidate_id"], p["replaced_id"], p["max_positions"], p["max_hold_days"]) for p in points}) == 7 * 8
    assert all(p["candidate_weight"] > 0 for p in points)
    assert all("replaced_id" in p for p in points)
    assert all(p["max_positions"] == 20 for p in points)
    assert all(p["max_hold_days"] == 30 for p in points)


def test_committed_r392_evidence_has_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")
    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar
    assert report["round"] == "R392"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["decision"]["status"] == "portfolio_slot_screen_complete_no_promotion"
    assert report["decision"]["active_version"] is None
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert report["no_leverage"] is True
    assert "不生成订单" in markdown
