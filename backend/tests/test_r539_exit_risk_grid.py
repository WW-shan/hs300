from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r539_exit_risk_grid.py"


def _module():
    spec = importlib.util.spec_from_file_location("r539_exit_risk_grid_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_source() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r539_exit_risk_grid_preregistration.json" in text
    assert "r538_expanded_portfolio_execution_grid_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_exit_grid_has_seven_fixed_variants() -> None:
    module = _module()
    rows = module.exit_grid()
    assert len(rows) == 7
    assert [row["variant_id"] for row in rows] == [
        "stop_05",
        "stop_08",
        "stop_12",
        "stop_15",
        "no_stop",
        "stop_08_trail_10",
        "stop_12_trail_10",
    ]
    assert rows[1]["stop_loss"] == -0.08
    assert rows[1]["trailing_stop"] is None


def test_exit_selection_uses_bootstrap_then_sharpe() -> None:
    module = _module()
    rows = [
        {
            "order": 0,
            "stats": {"annual_return": 0.15, "sharpe": 1.2, "max_drawdown": -0.10},
            "bootstrap": {"total_p05": 0.02},
            "trade_count": 100,
        },
        {
            "order": 1,
            "stats": {"annual_return": 0.14, "sharpe": 1.1, "max_drawdown": -0.12},
            "bootstrap": {"total_p05": 0.06},
            "trade_count": 100,
        },
    ]
    selected = module.select_exit_variant(rows)
    assert selected is not None
    assert selected["order"] == 1


def test_committed_r539_result_is_formal_and_preserves_recent_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r539_exit_risk_grid_preregistration.json"
    result = research / "r539_exit_risk_grid_result.json"
    markdown = research / "r539_exit_risk_grid_result.md"
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
    assert payload["metrics"]["selected"]["variant_id"] == "stop_12"
    assert payload["gate_results"]["failed_gate_paths"] == ["core_2x.recent_return"]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
