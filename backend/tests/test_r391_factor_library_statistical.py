from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r391_factor_library_statistical.py"
PREREG = EVIDENCE / "r391_factor_library_statistical_preregistration.json"
R390 = EVIDENCE / "r390_factor_library_screening.json"
REPORT = EVIDENCE / "r391_factor_library_statistical.json"
RESULT = EVIDENCE / "r391_factor_library_statistical_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r391_factor_library_statistical", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_is_frozen() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R391"
    assert payload["gates"]["nw_p_max"] == 0.10
    assert payload["gates"]["bh_q_max"] == 0.10
    assert payload["output_policy"]["status"] == "statistical_screening_complete_no_promotion"
    assert payload["output_policy"]["active_version"] is None


def test_candidates_are_exactly_r390_eligible_set() -> None:
    module = _module()
    r390 = json.loads(R390.read_text(encoding="utf-8"))
    expected = r390["candidate_audit"]["eligible_factor_ids"]

    assert module.candidate_ids() == expected


def test_core_ids_are_watch_alpha_only() -> None:
    module = _module()
    core = module.core_members()
    assert core
    assert all(row["role"] == "alpha" for row in core)
    assert all(row["membership_status"] == "watch" for row in core)
    assert module.core_ids() == [row["factor_id"] for row in core]


def test_committed_r391_evidence_has_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")

    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar

    assert report["round"] == "R391"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["decision"]["status"] == "statistical_screening_complete_no_promotion"
    assert report["decision"]["active_version"] is None
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert "不生成订单" in markdown
