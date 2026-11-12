from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from app.backtest.strategy import StrategyDependencyResolver
from app.factors.ext_factors import ensure_synced
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r366_strategies"
PREREG = EVIDENCE / "r366_neutralization_preregistration_v2.json"
PRECHECK = EVIDENCE / "run_r366_neutralization_preflight.py"
RUNNER = EVIDENCE / "run_r366_neutralization.py"
CANDIDATE_SCORING = {
    "ext_alt_hs300_stock_rel_industry_20d": 0.35,
    "ext_alt_hs300_industry_mom_20d": 0.15,
    "cf_weak_reversal_blend": 0.20,
    "uf_quality_value_defensive": 0.10,
    "uf_cash_quality": 0.20,
}
CONTROL_SCORING = {
    "ext_alt_hs300_stock_rel_industry_20d": 0.35,
    "ext_alt_hs300_industry_mom_20d": 0.15,
    "uf_quality_value_defensive": 0.30,
    "uf_cash_quality": 0.20,
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_engine() -> StrategyEngine:
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    return StrategyEngine(strategy_dirs=[STRATEGY_DIR])


def test_r366_strategies_hold_factor_contract_constant_and_only_neutralize_scores() -> None:
    engine = _load_engine()
    expected = {
        "industry": {"industry": "industry_l1", "styles": [], "exclude": [], "shrink": 1.0},
        "industry_size": {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
    }
    for variant, neutralization in expected.items():
        candidate = engine.get(f"ai_hs300_weak_reversal_blend_{variant}_h20_sl20")
        control = engine.get(f"ai_hs300_quality_{variant}_h20_sl20")
        assert candidate.meta["scoring"] == CANDIDATE_SCORING
        assert control.meta["scoring"] == CONTROL_SCORING
        assert candidate.basic_filter == control.basic_filter
        assert candidate.max_hold_days == control.max_hold_days == 20
        assert candidate.stop_loss == control.stop_loss == -0.20
        assert candidate.meta["neutralize"] == control.meta["neutralize"] == neutralization
        plan = StrategyDependencyResolver().resolve(
            candidate,
            params={},
            basic_filter=candidate.basic_filter,
            entry_signals=[],
            exit_signals=[],
            asset_type="stock",
        )
        if variant == "industry_size":
            assert "volume" in plan.base_columns and "turnover_rate" in plan.base_columns


def test_r366_preregistration_freezes_paired_neutralization_design() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["round"] == "R366"
    assert prereg["rule"]["neutralization_variants"] == ["industry", "industry_size"]
    assert prereg["rule"]["holding_sessions"] == 20
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["leverage"] == "none"
    assert prereg["parameter_sweep"] is False
    assert len(prereg["performance_gates"]) == 9


def test_r366_manifest_hashes_the_inputs_and_rejects_r362_control_substitution() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = _load_module(PRECHECK, "r366_preflight_contract")
    runner = _load_module(RUNNER, "r366_runner_contract")
    expected = prereg["frozen_inputs"]
    assert preflight.PRE_REG == runner.PRE_REG == PREREG
    assert preflight.OUTPUT == runner.PREFLIGHT
    assert preflight.input_fingerprints() == runner.input_fingerprints()
    assert expected["preflight_runner_sha256"]
    assert expected["backtest_runner_sha256"]
    assert expected["test_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert any("r365_weak_reversal_20d_backtest.json" in path for path in expected["prior_evidence_sha256"])
    assert not any("r362" in path for path in expected["prior_evidence_sha256"])


def test_r366_tests_and_results_are_research_only() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["decision"]["paper_target_generated"] is False
    assert prereg["decision"]["orders_generated"] is False
