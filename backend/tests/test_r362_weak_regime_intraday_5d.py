from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

from app.factors.ext_factors import ensure_synced
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r362_strategies"
RUNNER_PATH = EVIDENCE / "run_r362_weak_regime_intraday_5d.py"
PREREG_PATH = EVIDENCE / "r362_weak_regime_intraday_5d_preregistration.json"
CANDIDATE_ID = "ai_hs300_industry_relative_strength_intraday_reversal_weak_h5_sl20"
CONTROL_ID = "ai_hs300_industry_relative_strength_quality_weak_h5_sl20"
FACTOR_ID = "uf_intraday_reversal"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_strategies() -> tuple[object, object]:
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    engine = StrategyEngine(strategy_dirs=[STRATEGY_DIR])
    return engine.get(CANDIDATE_ID), engine.get(CONTROL_ID)


def test_r362_is_a_matched_five_session_holding_comparison() -> None:
    candidate, control = _load_strategies()
    candidate_scoring = {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        FACTOR_ID: 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    }
    control_scoring = {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }

    assert candidate.meta["scoring"] == candidate_scoring
    assert control.meta["scoring"] == control_scoring
    assert candidate.basic_filter == control.basic_filter
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert candidate.max_hold_days == control.max_hold_days == 5
    assert FACTOR_ID in candidate.required_features
    assert FACTOR_ID not in control.required_features


def test_r362_runner_freezes_weak_regime_execution_and_new_cache() -> None:
    runner = _load_module(RUNNER_PATH, "r362_runner_contract")
    config = runner.make_backtest_config(CANDIDATE_ID, date(2022, 5, 5), 3)

    assert runner.REGIME_FILTER == {"states": ["weak"]}
    assert config.regime_filter == runner.REGIME_FILTER
    assert config.matching == "open_t+1"
    assert config.max_positions == 10
    assert config.max_exposure_pct == 1.0
    assert config.fees_pct == 0.0006
    assert config.commission_pct == 0.0006
    assert config.stamp_tax_pct == 0.0010
    assert config.slippage_bps == 15.0
    assert runner.CACHE != ROOT / "data/research/r361_cache"
    assert runner.RESULT.name == "r362_weak_regime_intraday_5d_backtest.json"


def test_r362_preregistration_is_frozen_and_not_a_parameter_sweep() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))

    assert prereg["round"] == "R362"
    assert prereg["rule"]["holding_sessions"] == 5
    assert prereg["rule"]["stop_loss"] == -0.20
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["rule"]["candidate_strategy"] == CANDIDATE_ID
    assert prereg["rule"]["control_strategy"] == CONTROL_ID
    assert prereg["parameter_sweep"] is False
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["primary_window"]["cost_multipliers"] == [2, 3]
    assert len(prereg["primary_window"]["phase_starts"]) == 6
    assert prereg["frozen_inputs"]["data_fingerprints"]
    assert prereg["frozen_inputs"]["code_sha256"]
    assert prereg["frozen_artifacts"]


def test_r362_preflight_and_backtest_share_the_frozen_contract() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    preflight = _load_module(EVIDENCE / "run_r362_weak_regime_intraday_5d_preflight.py", "r362_preflight_contract")
    backtest = _load_module(RUNNER_PATH, "r362_backtest_contract")

    assert preflight.PRE_REG == backtest.PRE_REG == PREREG_PATH
    assert preflight.OUTPUT == backtest.PREFLIGHT
    assert preflight.input_fingerprints() == backtest.input_fingerprints()
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]
    assert prereg["frozen_inputs"]["isolation_helper_sha256"]
    assert prereg["frozen_inputs"]["test_sha256"]
    assert prereg["frozen_inputs"]["test_isolation_sha256"]
