from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r540_weighted_expanded_core.py"


def _module():
    spec = importlib.util.spec_from_file_location("r540_weighted_expanded_core_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r540_weighted_expanded_core_preregistration.json" in text
    assert "r539_exit_risk_grid_result.json" in text
    assert "r536a_finalist_cluster_fdr_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_weight_schemes_have_four_fixed_variants() -> None:
    module = _module()
    icir = {
        "a": 0.04,
        "b": 0.02,
        "c": -0.01,
        "d": 0.03,
        "e": 0.01,
        "f": 0.005,
        "g": 0.001,
        "h": 0.002,
        "i": 0.0005,
    }
    schemes = module.weight_schemes(icir)
    assert [row["scheme_id"] for row in schemes] == [
        "equal",
        "icir",
        "top8_icir",
        "top5_icir",
    ]
    assert abs(sum(schemes[1]["weights"].values()) - 1.0) < 1e-12
    assert schemes[2]["weights"]["c"] == 0.0
    assert schemes[3]["weights"]["a"] > 0
    assert list(schemes[3]["weights"].values()).count(0.0) == 4


def test_committed_r540_result_is_formal_and_keeps_no_strategy_signal() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r540_weighted_expanded_core_preregistration.json"
    result = research / "r540_weighted_expanded_core_result.json"
    markdown = research / "r540_weighted_expanded_core_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "project_formal"
    assert payload["conclusion"] == "rejected"
    assert payload["formal_evidence"]["persistence"] == "saved"
    assert payload["formal_evidence"]["lookahead"] == "passed"
    assert payload["formal_evidence"]["execution_timing"] == "passed"
    assert payload["preregistration_sha256"] == hashlib.sha256(
        prereg.read_bytes()
    ).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert payload["metrics"]["selected"]["scheme_id"] == "equal"
    assert payload["gate_results"]["failed_gate_paths"] == [
        "core_2x.recent_return",
        "recent_excess_vs_equal",
    ]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
