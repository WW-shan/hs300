from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/research_r534_regime_gated_core.py"


def _module():
    spec = importlib.util.spec_from_file_location("r534_regime_gated_core_test", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_declares_formal_contract_and_frozen_sources() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert 'RESEARCH_TYPE = "project_formal"' in text
    assert "r534b_regime_gated_core_preregistration.json" in text
    assert "r533a_core_formal_portfolio_result.json" in text
    assert "r533b_core_formal_gate_reaudit_result.json" in text
    assert "run_with_evidence" in text
    assert "require_formal_evidence" in text


def test_six_fixed_regime_rules_are_ordered() -> None:
    module = _module()
    rules = module.regime_rules()
    assert [row["rule_id"] for row in rules] == [
        "always_on",
        "exclude_weak",
        "exclude_weak_pair",
        "score_min_20",
        "score_min_30",
        "exclude_weak_pair_min20",
    ]
    assert rules[0]["regime_filter"] is None
    assert rules[2]["regime_filter"]["states"] == ["strong", "lean_strong", "range"]
    assert rules[5]["regime_filter"] == {
        "states": ["strong", "lean_strong", "range"],
        "min_score": 20,
    }


def test_training_selection_prefers_less_severe_bootstrap_drawdown() -> None:
    module = _module()
    rows = [
        {
            "rule_id": "a",
            "order": 0,
            "stats": {"annual_return": 0.20, "sharpe": 1.4, "max_drawdown": -0.10},
            "bootstrap": {"mdd_p05": -0.22, "total_p05": 0.15},
            "trade_count": 120,
        },
        {
            "rule_id": "b",
            "order": 1,
            "stats": {"annual_return": 0.18, "sharpe": 1.3, "max_drawdown": -0.11},
            "bootstrap": {"mdd_p05": -0.12, "total_p05": 0.10},
            "trade_count": 110,
        },
        {
            "rule_id": "c",
            "order": 2,
            "stats": {"annual_return": 0.175, "sharpe": 1.2, "max_drawdown": -0.12},
            "bootstrap": {"mdd_p05": -0.15, "total_p05": 0.05},
            "trade_count": 100,
        },
    ]
    selected = module.select_regime_rule(rows, baseline_training_annual=0.1991)
    assert selected is not None
    assert selected["rule_id"] == "b"


def test_training_eligibility_rejects_return_drop_or_weak_bootstrap() -> None:
    module = _module()
    row = {
        "rule_id": "x",
        "order": 0,
        "stats": {"annual_return": 0.175, "sharpe": 1.2, "max_drawdown": -0.12},
        "bootstrap": {"mdd_p05": -0.15, "total_p05": 0.01},
        "trade_count": 100,
    }
    assert module.regime_rule_eligible(row, baseline_training_annual=0.1991)
    row["bootstrap"]["total_p05"] = -0.01
    assert not module.regime_rule_eligible(row, baseline_training_annual=0.1991)


def test_frozen_source_selected_parameters_are_supported() -> None:
    module = _module()
    source = module.load_source(ROOT)
    selected = source["r533a"]["metrics"]["selected"]
    assert selected["max_positions"] == 10
    assert selected["max_hold_days"] == 60
    assert source["r533b"]["conclusion"] == "rejected"
    assert source["r533b"]["metrics"]["failed_gate_paths"] == [
        "core_2x.recent_return",
        "stress.recent_return",
    ]


def test_runtime_bundle_exposes_r533_helper_to_training(monkeypatch) -> None:
    module = _module()

    class FakeR533:
        @staticmethod
        def load_runtime_inputs(root):
            return {"panel": None}

    class FakeP5:
        pass

    def fake_load(name):
        return FakeR533() if name.endswith("r533_core_formal_portfolio") else FakeP5()

    monkeypatch.setattr(module, "_load_script_module", fake_load)
    loaded = module.load_runtime(ROOT)
    assert loaded["runtime"]["r533"] is loaded["r533"]


def test_committed_r534b_result_is_formal_and_preserves_rejection() -> None:
    import hashlib
    import json

    research = ROOT / "data/factor_research/research"
    prereg = research / "r534b_regime_gated_core_preregistration.json"
    result = research / "r534b_regime_gated_core_result.json"
    markdown = research / "r534b_regime_gated_core_result.md"
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
    assert payload["metrics"]["selected"]["rule_id"] == "exclude_weak_pair"
    assert payload["metrics"]["regime_end"] == "2026-10-14"
    assert set(payload["gate_results"]["failed_gate_paths"]) == {
        "core_2x.sharpe",
        "core_2x.recent_return",
        "core_2x.minimum_fresh_starts",
        "stress.recent_return",
        "validation_annual_positive",
        "recent_excess_vs_always_on",
    }
    assert payload["output_policy"] == {
        "active_version": None,
        "orders_generated": False,
        "paper_target_generated": False,
        "production_enabled": False,
    }
