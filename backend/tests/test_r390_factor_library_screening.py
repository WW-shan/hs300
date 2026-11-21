from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = EVIDENCE / "run_r390_factor_library_screening.py"
PREREG = EVIDENCE / "r390_factor_library_screening_preregistration.json"
REPORT = EVIDENCE / "r390_factor_library_screening.json"
RESULT = EVIDENCE / "r390_factor_library_screening_result.md"


def _module():
    spec = importlib.util.spec_from_file_location("r390_factor_library_screening", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_preregistration_is_frozen_and_no_promotion() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == sidecar
    assert payload["round"] == "R390"
    assert payload["exclusions"]["require_portfolio_ready"] is True
    assert payload["exclusions"]["exclude_p5_p8_studied"] is True
    assert payload["cost_protocol"]["five_x_can_hard_reject"] is False
    assert payload["output_policy"]["status"] == "screening_complete_no_promotion"
    assert payload["output_policy"]["active_version"] is None


def test_candidate_builder_is_deterministic_and_excludes_studied_fixed_blocked() -> None:
    module = _module()
    first = module.eligible_factors()
    second = module.eligible_factors()

    assert first == second
    assert first
    assert first == sorted(first)
    assert len(first) == len(set(first))

    audit = module.exclusion_audit()
    ready = set(module.ready_factors())
    fixed = set(module.fixed_factors())
    blocked = set(module.blocked_factors())
    studied = module.studied_factors()
    assert fixed & set(first) == set()
    assert blocked & set(first) == set()
    assert studied & set(first) == set()
    assert set(first) <= ready
    assert audit["eligible_count"] == len(first)


def test_candidate_builder_requires_portfolio_ready(monkeypatch) -> None:
    module = _module()
    ready = module.ready_factors()
    assert ready
    # Removing one ready factor must remove it from eligible output.
    victim = ready[0]
    monkeypatch.setattr(module, "ready_factors", lambda: ready[1:])
    assert victim not in module.eligible_factors()


def test_family_representatives_are_deterministic() -> None:
    module = _module()
    reps = module.family_representatives()
    assert reps == module.family_representatives()
    assert len(reps) == len(set(reps))
    assert set(reps) <= set(module.eligible_factors())


def test_committed_r390_evidence_matches_runner_and_no_execution_policy() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    markdown = RESULT.read_text(encoding="utf-8")

    for path in (PREREG, REPORT, RESULT):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar

    assert report["round"] == "R390"
    assert report["runner_sha256"] == hashlib.sha256(RUNNER.read_bytes()).hexdigest()
    assert report["decision"]["status"] == "screening_complete_no_promotion"
    assert report["decision"]["active_version"] is None
    assert report["decision"]["orders_generated"] is False
    assert report["decision"]["paper_target_generated"] is False
    assert report["decision"]["production_enabled"] is False
    assert report["no_leverage"] is True
    assert "不生成订单" in markdown
