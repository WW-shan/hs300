from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r537_expanded_factor_portfolio.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "r537_expanded_factor_portfolio_test", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r537c_expanded_factor_portfolio_preregistration.json" in text
    assert "r533a_core_formal_portfolio_result.json" in text
    assert "r536a_finalist_cluster_fdr_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_expanded_portfolio_has_thirteen_frozen_representatives() -> None:
    module = _module()
    rows = module.load_expanded_representatives(ROOT)
    assert len(rows) == 13
    assert len({row["family_id"] for row in rows}) == 13
    assert all(row["parameter"] is not None for row in rows)
    assert all(row["cluster_size"] >= 1 for row in rows)


def test_committed_r537c_result_is_formal_and_preserves_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r537c_expanded_factor_portfolio_preregistration.json"
    result = research / "r537c_expanded_factor_portfolio_result.json"
    markdown = research / "r537c_expanded_factor_portfolio_result.md"
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
    assert payload["metrics"]["expanded_representative_count"] == 13
    assert payload["gate_results"]["failed_gate_paths"] == [
        "core_2x.sharpe",
        "core_2x.recent_return",
        "control_2x.sharpe",
        "stress.recent_return",
        "concentration.top_industry_gross_share",
    ]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
