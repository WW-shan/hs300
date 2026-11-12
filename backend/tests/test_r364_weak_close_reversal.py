from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.factors.ext_factors import ensure_synced
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r364_strategies"
RUNNER_PATH = EVIDENCE / "run_r364_weak_close_reversal.py"
PREREG_PATH = EVIDENCE / "r364_weak_close_reversal_preregistration.json"
CANDIDATE_ID = "ai_hs300_industry_relative_strength_close_reversal_weak_h5_sl20"
CONTROL_ID = "ai_hs300_industry_relative_strength_quality_weak_h5_sl20"
FACTOR_ID = "uf_close_position_reversal"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_factor():
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")


def test_close_position_reversal_factor_is_high_near_daily_low() -> None:
    formula = json.loads((ROOT / "data/user_data/custom_factors/uf_close_position_reversal.json").read_text())[
        "formula"
    ]
    compiled = compile_formula(formula)
    assert compiled.ok
    assert compiled.dependencies == {"high", "low", "close"}
    frame = pl.DataFrame({"symbol": ["A", "B", "C"], "date": [date(2026, 1, 5)] * 3,
                          "high": [12.0] * 3, "low": [8.0] * 3, "close": [8.0, 12.0, 10.0]})
    transformed = compiled.frame_transform(frame)
    assert transformed is not None
    assert transformed[FACTOR_COLUMN].to_list() == [1.0, 0.0, 0.5]


def test_r364_candidate_replaces_only_the_frozen_twenty_percent_leg() -> None:
    _load_factor()
    engine = StrategyEngine(strategy_dirs=[STRATEGY_DIR])
    candidate = engine.get(CANDIDATE_ID)
    control = engine.get(CONTROL_ID)
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        FACTOR_ID: 0.20,
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
    assert candidate.max_hold_days == control.max_hold_days == 5
    assert candidate.stop_loss == control.stop_loss == -0.20
    assert FACTOR_ID in candidate.required_features


def test_r364_preregistration_freezes_weak_five_day_single_factor_ablation() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    assert prereg["round"] == "R364"
    assert prereg["rule"]["factor"] == FACTOR_ID
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["rule"]["holding_sessions"] == 5
    assert prereg["parameter_sweep"] is False
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["primary_window"]["cost_multipliers"] == [2, 3]
    assert len(prereg["primary_window"]["phase_starts"]) == 6


def test_r364_preflight_and_runner_share_frozen_manifest() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    preflight = _load_module(EVIDENCE / "run_r364_weak_close_reversal_preflight.py", "r364_preflight_test")
    runner = _load_module(RUNNER_PATH, "r364_runner_test")
    assert preflight.PRE_REG == runner.PRE_REG == PREREG_PATH
    assert preflight.OUTPUT == runner.PREFLIGHT
    assert preflight.input_fingerprints() == runner.input_fingerprints()
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]
    assert prereg["frozen_inputs"]["test_sha256"]
    assert prereg["frozen_inputs"]["test_isolation_sha256"]


def test_r364_reports_the_component_ablation_baselines() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    baselines = prereg["frozen_inputs"]["prior_evidence_sha256"]
    assert any("r362_weak_regime_intraday_5d_backtest.json" in path for path in baselines)
    assert any("r363_weak_reversal_blend_backtest.json" in path for path in baselines)
