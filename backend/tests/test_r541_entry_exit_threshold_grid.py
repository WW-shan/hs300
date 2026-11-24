from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r541_entry_exit_threshold_grid.py"


def _module():
    spec = importlib.util.spec_from_file_location("r541_entry_exit_threshold_grid_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_source() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r541_entry_exit_threshold_grid_preregistration.json" in text
    assert "r539_exit_risk_grid_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_threshold_grid_has_nine_fixed_pairs() -> None:
    module = _module()
    rows = module.threshold_grid()
    assert len(rows) == 9
    assert [(row["entry_score"], row["exit_score"]) for row in rows] == [
        (60, 30), (60, 40), (60, 50),
        (70, 30), (70, 40), (70, 50),
        (80, 30), (80, 40), (80, 50),
    ]


def test_committed_r541_result_is_formal_and_preserves_recent_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r541_entry_exit_threshold_grid_preregistration.json"
    result = research / "r541_entry_exit_threshold_grid_result.json"
    markdown = research / "r541_entry_exit_threshold_grid_result.md"
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
    assert payload["metrics"]["selected"]["entry_score"] == 60
    assert payload["metrics"]["selected"]["exit_score"] == 40
    assert payload["gate_results"]["failed_gate_paths"] == [
        "core_2x.recent_return",
        "recent_excess_vs_default",
    ]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
