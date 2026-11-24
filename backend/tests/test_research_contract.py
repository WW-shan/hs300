from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_grandfather_manifest_matches_frozen_runner_hashes() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "data/factor_research/research/research_contract_grandfather_2026-11-24.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == len(payload["rows"])
    assert payload["rows"]
    for row in payload["rows"]:
        runner = root / row["path"]
        assert runner.is_file(), row["path"]
        assert hashlib.sha256(runner.read_bytes()).hexdigest() == row["sha256"], row["path"]
        assert row["disposition"] == "legacy_grandfathered"
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(path.read_bytes()).hexdigest()


def _policy() -> dict:
    return {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }


def _prereg(**updates):
    payload = {
        "schema_version": 1,
        "research_id": "R-GBM-TEST-001",
        "research_type": "synthetic_market_stress",
        "question": "Does the signal survive a synthetic market?",
        "hypothesis": "Signal performance is not only drift.",
        "study_scope": "research_only",
        "runner": "scripts/research_gbm_market_stress.py",
        "runner_sha256": "a" * 64,
        "data_source": "synthetic_gbm",
        "input_hashes": {},
        "parameters": {"paths": 4},
        "sample": {"start": "2022-01-01", "end": "2026-11-20"},
        "execution": {
            "matching": "open_t+1",
            "cost_multiplier": 2,
            "leverage": "none",
            "max_exposure_pct": 1.0,
        },
        "synthetic_model": {
            "model": "geometric_brownian_motion",
            "parameters": {"annual_drift": 0.05, "annual_volatility": 0.2},
            "seed": 20261124,
            "path_count": 4,
            "benchmark": "same_path_equal_weight",
        },
        "gates": {"beat_equal_weight": True},
        "falsification_checks": ["seeded_path_replay"],
        "output_policy": _policy(),
        "created_at": "2026-11-24T00:00:00Z",
    }
    payload.update(updates)
    return payload


def _result(**updates):
    payload = {
        "schema_version": 1,
        "research_id": "R-GBM-TEST-001",
        "research_type": "synthetic_market_stress",
        "runner": "scripts/research_gbm_market_stress.py",
        "runner_sha256": "a" * 64,
        "preregistration_sha256": "b" * 64,
        "generated_at": "2026-11-24T01:00:00Z",
        "conclusion": "inconclusive",
        "gate_results": {"beat_equal_weight": True},
        "metrics": {"paths": 4},
        "source_result_hashes": [],
        "formal_evidence": None,
        "known_limits": ["synthetic paths do not establish live performance"],
        "negative_knowledge": [],
        "output_policy": _policy(),
    }
    payload.update(updates)
    return payload


def test_research_preregistration_rejects_execution_outputs() -> None:
    from app.factor_research.research_contract import ResearchPreregistration

    payload = _prereg(output_policy={**_policy(), "active_version": 1})
    try:
        ResearchPreregistration.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("active_version must be rejected")


def test_synthetic_market_stress_requires_seed_paths_and_benchmark() -> None:
    from app.factor_research.research_contract import ResearchPreregistration

    payload = _prereg()
    payload["synthetic_model"] = {
        "model": "geometric_brownian_motion",
        "parameters": {"annual_drift": 0.05},
        "seed": 1,
        "path_count": 1,
        "benchmark": "",
    }
    try:
        ResearchPreregistration.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("synthetic market contract must require paths and benchmark")


def test_project_formal_rejects_leverage_and_excess_exposure() -> None:
    from app.factor_research.research_contract import ResearchPreregistration

    payload = _prereg(
        research_type="project_formal",
        data_source="project",
        synthetic_model=None,
        execution={
            "matching": "close_t",
            "cost_multiplier": 1,
            "leverage": "allowed",
            "max_exposure_pct": 1.5,
        },
    )
    try:
        ResearchPreregistration.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("formal project execution must be unlevered open_t+1 at 2x cost")


def test_result_requires_matching_preregistration_hash() -> None:
    from app.factor_research.research_contract import (
        ResearchPreregistration,
        ResearchResultEnvelope,
        validate_result_for_preregistration,
    )

    prereg = ResearchPreregistration.model_validate(_prereg())
    result = ResearchResultEnvelope.model_validate(_result())
    errors = validate_result_for_preregistration(
        prereg, result, preregistration_sha256="c" * 64
    )
    assert "preregistration_sha256 mismatch" in errors


def test_project_formal_result_requires_all_formal_evidence() -> None:
    from app.factor_research.research_contract import ResearchResultEnvelope

    payload = _result(
        research_type="project_formal",
        formal_evidence={
            "persistence": "saved",
            "lookahead": "failed",
            "execution_timing": "passed",
        },
    )
    try:
        ResearchResultEnvelope.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("project_formal evidence must pass all three audits")


def test_diagnostic_result_cannot_claim_formal_admission() -> None:
    from app.factor_research.research_contract import ResearchResultEnvelope

    payload = _result(
        research_type="diagnostic_simulation",
        conclusion="admitted_for_research",
        known_limits=["hand-written simulation"],
    )
    try:
        ResearchResultEnvelope.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("diagnostic simulation must not be admitted")


def test_non_return_result_cannot_publish_return_metrics() -> None:
    from app.factor_research.research_contract import ResearchResultEnvelope

    payload = _result(
        research_type="non_return_research",
        metrics={"annual_return": 0.2},
    )
    try:
        ResearchResultEnvelope.model_validate(payload)
    except ValueError:
        return
    raise AssertionError("non-return research must reject return metrics")


def _write_prereg(tmp_path: Path):
    import hashlib
    import json

    from app.factor_research.research_contract import ResearchPreregistration

    runner = tmp_path / "scripts/research_gbm_market_stress.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("# runner\n", encoding="utf-8")
    prereg_path = tmp_path / "data/factor_research/research/r-gbm-test-001_preregistration.json"
    prereg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _prereg()
    payload["runner"] = "scripts/research_gbm_market_stress.py"
    payload["runner_sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    prereg_path.write_text(raw, encoding="utf-8")
    prereg_path.with_name(prereg_path.name + ".sha256").write_text(
        hashlib.sha256(raw.encode()).hexdigest() + "  " + prereg_path.name + "\n",
        encoding="ascii",
    )
    return prereg_path, ResearchPreregistration.model_validate(payload)


def test_publish_refuses_existing_result(tmp_path: Path) -> None:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    prereg_path, prereg = _write_prereg(tmp_path)
    result = ResearchResultEnvelope.model_validate(_result())
    target = tmp_path / "data/factor_research/research/r-gbm-test-001_result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}\n", encoding="utf-8")
    try:
        publish_research_artifacts(
            tmp_path,
            preregistration_path=prereg_path,
            result=result,
            markdown="# result\n",
            operation="research_contract_test",
        )
    except FileExistsError:
        return
    raise AssertionError("publish must refuse an existing result")


def test_republish_same_bytes_is_idempotent(tmp_path: Path) -> None:
    import hashlib

    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    prereg_path, prereg = _write_prereg(tmp_path)
    prereg_hash = hashlib.sha256(prereg_path.read_bytes()).hexdigest()
    result = ResearchResultEnvelope.model_validate(
        _result(preregistration_sha256=prereg_hash)
    )
    first = publish_research_artifacts(
        tmp_path,
        preregistration_path=prereg_path,
        result=result,
        markdown="# result\n",
        operation="research_contract_test",
    )
    second = publish_research_artifacts(
        tmp_path,
        preregistration_path=prereg_path,
        result=result,
        markdown="# result\n",
        operation="research_contract_test",
    )
    assert first.result_sha256 == second.result_sha256
    assert first.ledger_record_id == second.ledger_record_id


def test_replaced_result_bytes_fail_closed(tmp_path: Path) -> None:
    from app.factor_research.research_contract import (
        ResearchResultEnvelope,
        publish_research_artifacts,
    )

    prereg_path, _ = _write_prereg(tmp_path)
    result = ResearchResultEnvelope.model_validate(
        _result(preregistration_sha256=__import__("hashlib").sha256(prereg_path.read_bytes()).hexdigest())
    )
    first = publish_research_artifacts(
        tmp_path,
        preregistration_path=prereg_path,
        result=result,
        markdown="# result\n",
        operation="research_contract_test",
    )
    first.result_path.write_text("{}\n", encoding="utf-8")
    try:
        publish_research_artifacts(
            tmp_path,
            preregistration_path=prereg_path,
            result=result,
            markdown="# result\n",
            operation="research_contract_test",
        )
    except ValueError:
        return
    raise AssertionError("immutable result drift must fail closed")
