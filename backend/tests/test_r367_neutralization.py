from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from app.backtest.strategy import StrategyDependencyResolver, effective_neutralize
from app.factors.ext_factors import ensure_synced
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r367_strategies"
PREREG = EVIDENCE / "r367_size_neutralization_preregistration.json"
PRECHECK = EVIDENCE / "run_r367_size_neutralization_preflight.py"
RUNNER = EVIDENCE / "run_r367_size_neutralization.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _engine() -> StrategyEngine:
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    return StrategyEngine(strategy_dirs=[STRATEGY_DIR])


def test_r367_only_neutralizes_size_for_paired_candidate_and_control() -> None:
    engine = _engine()
    candidate = engine.get("ai_hs300_weak_reversal_blend_size_h20_sl20")
    control = engine.get("ai_hs300_quality_size_h20_sl20")
    expected_neutralization = {"industry": None, "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0}
    assert effective_neutralize(candidate.meta) == effective_neutralize(control.meta) == expected_neutralization
    assert candidate.meta["neutralize"] == control.meta["neutralize"]
    assert "industry" not in candidate.meta["neutralize"]
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "cf_weak_reversal_blend": 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    }
    assert control.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }
    assert candidate.basic_filter == control.basic_filter
    assert candidate.max_hold_days == control.max_hold_days == 20
    assert candidate.stop_loss == control.stop_loss == -0.20
    plan = StrategyDependencyResolver().resolve(
        candidate, params={}, basic_filter=candidate.basic_filter,
        entry_signals=[], exit_signals=[], asset_type="stock"
    )
    assert {"volume", "turnover_rate"}.issubset(plan.base_columns)


def test_r367_preregistration_freezes_size_only_experiment_and_matches_r365_r366() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["round"] == "R367"
    assert prereg["rule"]["neutralization"] == {
        "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0
    }
    assert "industry" not in prereg["rule"]["neutralization"]
    assert prereg["rule"]["holding_sessions"] == 20
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["leverage"] == "none"
    assert prereg["parameter_sweep"] is False
    assert any("r365_weak_reversal_20d_backtest.json" in p for p in prereg["frozen_inputs"]["prior_evidence_sha256"])
    assert any("r366_neutralization_backtest.json" in p for p in prereg["frozen_inputs"]["prior_evidence_sha256"])
    assert not any("r362_" in p for p in prereg["frozen_inputs"]["prior_evidence_sha256"])


def test_r367_preflight_and_runner_use_the_same_frozen_manifest() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = _load_module(PRECHECK, "r367_preflight_contract")
    runner = _load_module(RUNNER, "r367_runner_contract")
    assert preflight.PRE_REG == runner.PRE_REG == PREREG
    assert preflight.OUTPUT == runner.PREFLIGHT
    assert preflight.input_fingerprints() == runner.input_fingerprints()
    assert prereg["frozen_inputs"]["test_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]


def test_r367_research_decision_is_unlevered_and_never_production_enabled() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["leverage"] == "none"
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["decision"]["paper_target_generated"] is False
    assert prereg["decision"]["orders_generated"] is False
