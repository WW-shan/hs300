from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r538_expanded_portfolio_execution_grid.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "r538_expanded_portfolio_execution_grid_test", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r538_expanded_portfolio_execution_grid_preregistration.json" in text
    assert "r537c_expanded_factor_portfolio_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_execution_grid_has_nine_candidates_and_industry_caps() -> None:
    module = _module()
    rows = module.execution_grid()
    assert len(rows) == 9
    assert {(row["max_positions"], row["max_hold_days"], row["industry_cap"]) for row in rows} == {
        (10, 20, 3),
        (10, 30, 3),
        (10, 60, 3),
        (20, 20, 6),
        (20, 30, 6),
        (20, 60, 6),
        (30, 20, 9),
        (30, 30, 9),
        (30, 60, 9),
    }


def test_execution_selection_is_deterministic_and_bootstrap_first() -> None:
    module = _module()
    rows = [
        {
            "max_positions": 10,
            "max_hold_days": 60,
            "stats": {"annual_return": 0.15, "sharpe": 1.2, "max_drawdown": -0.10},
            "bootstrap": {"total_p05": 0.02},
            "trade_count": 100,
        },
        {
            "max_positions": 20,
            "max_hold_days": 30,
            "stats": {"annual_return": 0.14, "sharpe": 1.1, "max_drawdown": -0.12},
            "bootstrap": {"total_p05": 0.06},
            "trade_count": 120,
        },
    ]
    selected = module.select_training_execution(rows)
    assert selected is not None
    assert selected["max_positions"] == 20
    assert selected["max_hold_days"] == 30


def test_committed_r538_result_is_formal_and_preserves_recent_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r538_expanded_portfolio_execution_grid_preregistration.json"
    result = research / "r538_expanded_portfolio_execution_grid_result.json"
    markdown = research / "r538_expanded_portfolio_execution_grid_result.md"
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
    assert payload["metrics"]["selected"]["max_positions"] == 10
    assert payload["metrics"]["selected"]["max_hold_days"] == 60
    assert payload["metrics"]["selected"]["industry_cap"] == 3
    assert payload["gate_results"]["failed_gate_paths"] == [
        "core_2x.recent_return",
        "stress.recent_return",
    ]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
