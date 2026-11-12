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
STRATEGY_DIR = EVIDENCE / "r365_strategies"
RUNNER_PATH = EVIDENCE / "run_r365_weak_reversal_20d.py"
PREREG_PATH = EVIDENCE / "r365_weak_reversal_20d_preregistration.json"
CANDIDATE_ID = "ai_hs300_industry_relative_strength_weak_reversal_blend_h20_sl20"
CONTROL_ID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
BLEND_ID = "cf_weak_reversal_blend"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_strategies():
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    engine = StrategyEngine(strategy_dirs=[ROOT / "data/strategies/ai", STRATEGY_DIR])
    return engine.get(CANDIDATE_ID), engine.get(CONTROL_ID)


def test_r365_uses_the_r363_blend_with_a_twenty_session_cap() -> None:
    candidate, control = _load_strategies()
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        BLEND_ID: 0.20,
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


def test_r365_runner_uses_weak_t1_filter_and_two_x_three_x_costs() -> None:
    runner = _load_module(RUNNER_PATH, "r365_runner_contract")
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
    assert runner.CACHE.name == "r365_cache"


def test_r365_preregistration_freezes_twenty_day_horizon_without_sweep() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    assert prereg["round"] == "R365"
    assert prereg["rule"]["holding_sessions"] == 20
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["parameter_sweep"] is False
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["primary_window"]["cost_multipliers"] == [2, 3]
    assert len(prereg["primary_window"]["phase_starts"]) == 6
    assert "R363" in prereg["research_basis"]
    assert prereg["primary_metric"]["name"] == "difference_in_differences_annual"
    assert len(prereg["performance_gates"]) == 9


def test_r365_preflight_and_backtest_share_the_frozen_manifest() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    preflight = _load_module(EVIDENCE / "run_r365_weak_reversal_20d_preflight.py", "r365_preflight_contract")
    runner = _load_module(RUNNER_PATH, "r365_backtest_contract")
    assert preflight.PRE_REG == runner.PRE_REG == PREREG_PATH
    assert preflight.OUTPUT == runner.PREFLIGHT
    assert preflight.input_fingerprints() == runner.input_fingerprints()
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]
    assert prereg["frozen_inputs"]["test_sha256"]
    assert prereg["frozen_inputs"]["test_isolation_sha256"]
    assert any("r363_weak_reversal_blend_backtest.json" in p for p in prereg["frozen_inputs"]["prior_evidence_sha256"])
    assert not any("r362" in p for p in prereg["frozen_inputs"]["prior_evidence_sha256"])
    assert "data/user_data/custom_factors/cf_weak_reversal_blend.json" in prereg["frozen_artifacts"]
    assert any("r365_strategies/" in p for p in prereg["frozen_artifacts"])


def test_r365_reports_matched_five_to_twenty_day_horizon_effects() -> None:
    runner = _load_module(RUNNER_PATH, "r365_horizon_comparison")
    five_day = {
        "candidate": {"2x": {"annual": 0.12, "sharpe": 0.58, "mdd": -0.23}, "recent_2x": -0.07, "phase_2x": {}},
        "control": {"2x": {"annual": 0.08, "sharpe": 0.45, "mdd": -0.26}, "recent_2x": -0.11, "phase_2x": {}},
    }
    twenty_day = {
        "candidate": {"2x": {"annual": 0.10, "sharpe": 0.50, "mdd": -0.25}, "recent_2x": -0.03, "phase_2x": {}},
        "control": {"2x": {"annual": 0.09, "sharpe": 0.47, "mdd": -0.24}, "recent_2x": -0.02, "phase_2x": {}},
    }

    comparison = runner.compare_horizon_metrics(twenty_day, five_day)

    assert abs(comparison["candidate"]["r365_minus_r363_annual"] + 0.02) < 1e-12
    assert abs(comparison["control"]["r365_minus_r363_annual"] - 0.01) < 1e-12
    assert abs(comparison["candidate_minus_control_annual_effect_20d"] - 0.01) < 1e-12
    assert abs(comparison["candidate_minus_control_annual_effect_5d"] - 0.04) < 1e-12
    assert abs(comparison["difference_in_differences_annual"] + 0.03) < 1e-12
    assert abs(comparison["candidate"]["r365_minus_r363_recent_return"] - 0.04) < 1e-12


def test_r365_declares_only_research_evidence_and_no_production_outputs() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["decision"]["leverage"] == "none"
    assert prereg["parameter_sweep"] is False
