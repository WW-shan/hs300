from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r533b_core_formal_gate_reaudit.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "r533b_core_formal_gate_reaudit_test", RUNNER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_derived_contract_and_r533a_source() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "derived_analysis_only"' in text
    assert "r533b_core_formal_gate_reaudit_preregistration.json" in text
    assert "r533a_core_formal_portfolio_result.json" in text


def test_gate_candidate_maps_backtest_metric_names_to_v2_policy_names() -> None:
    module = _module()
    source = {
        "max_positions": 10,
        "is_multi_asset": False,
        "data_integrity": {"pass": True, "checks": {}},
        "no_leverage": True,
        "main_2x": {
            "annual_return": 0.14,
            "sharpe": 1.03,
            "max_drawdown": -0.145,
            "total_return": 0.78,
            "recent": -0.048,
            "year_2022": 0.06,
            "year_2023": 0.07,
            "fresh_starts": [0.1, 0.2, 0.3, 0.01, -0.02, -0.03],
        },
        "control_2x": {"annual": 0.128, "sharpe": 0.98, "recent": -0.038},
        "stress_3x": {
            "cost_factor": 3,
            "annual_return": 0.135,
            "sharpe": 0.98,
            "max_drawdown": -0.147,
            "total_return": 0.73,
            "recent": -0.056,
        },
        "extreme_5x": {
            "cost_factor": 5,
            "annual_return": 0.12,
            "sharpe": 0.88,
            "max_drawdown": -0.152,
            "total_return": 0.63,
            "recent": -0.071,
        },
        "concentration": {},
        "weights": {"max_weight_sum": 1.0, "min_target_weight": 0.0},
    }
    candidate = module.normalize_gate_candidate(source)
    assert candidate["main_2x"]["annual"] == 0.14
    assert candidate["main_2x"]["mdd"] == -0.145
    assert candidate["stress_3x"]["annual"] == 0.135
    assert candidate["stress_3x"]["mdd"] == -0.147
    assert candidate["extreme_5x"]["annual"] == 0.12
    assert candidate["extreme_5x"]["mdd"] == -0.152


def test_frozen_source_candidate_has_validation_positive_evidence() -> None:
    module = _module()
    source = module.load_source(ROOT)
    assert source["conclusion"] == "rejected"
    assert source["metrics"]["confirmation"]["validation"]["stats"]["annual_return"] > 0
    assert source["gate_results"]["failed_gate_paths"]


def test_committed_r533b_reaudit_only_fails_recent_stress_gates() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r533b_core_formal_gate_reaudit_preregistration.json"
    result = research / "r533b_core_formal_gate_reaudit_result.json"
    markdown = research / "r533b_core_formal_gate_reaudit_result.md"
    for path in (prereg, result, markdown):
        sidecar = path.with_name(path.name + ".sha256")
        assert sidecar.read_text(encoding="ascii").split()[0] == hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    payload = json.loads(result.read_text(encoding="utf-8"))
    prereg_payload = json.loads(prereg.read_text(encoding="utf-8"))
    assert payload["research_type"] == "derived_analysis_only"
    assert payload["conclusion"] == "rejected"
    assert payload["preregistration_sha256"] == hashlib.sha256(
        prereg.read_bytes()
    ).hexdigest()
    assert payload["runner_sha256"] == prereg_payload["runner_sha256"]
    assert payload["metrics"]["failed_gate_paths"] == [
        "core_2x.recent_return",
        "stress.recent_return",
    ]
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
