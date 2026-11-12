from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import polars as pl

from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.factors.ext_factors import ensure_synced
from app.factors.registry import get_factor
from app.factors.store import load_into_registry
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import materialize_scoring_columns

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
STRATEGY_DIR = EVIDENCE / "r363_strategies"
RUNNER_PATH = EVIDENCE / "run_r363_weak_reversal_blend.py"
PREREG_PATH = EVIDENCE / "r363_weak_reversal_blend_preregistration.json"
CANDIDATE_ID = "ai_hs300_industry_relative_strength_weak_reversal_blend_h5_sl20"
CONTROL_ID = "ai_hs300_industry_relative_strength_quality_weak_h5_sl20"
CLOSE_REVERSAL_ID = "uf_close_position_reversal"
BLEND_ID = "cf_weak_reversal_blend"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_factors() -> set[str]:
    ensure_synced(ROOT / "data")
    load_into_registry(ROOT / "data")
    return {CLOSE_REVERSAL_ID, BLEND_ID}


def test_close_position_reversal_is_high_when_close_is_near_daily_low() -> None:
    formula = json.loads((ROOT / "data/user_data/custom_factors/uf_close_position_reversal.json").read_text())[
        "formula"
    ]
    compiled = compile_formula(formula)
    assert compiled.ok
    assert compiled.dependencies == {"high", "low", "close"}

    frame = pl.DataFrame({"symbol": ["A", "B", "C"], "date": [date(2026, 1, 5)] * 3,
                          "high": [12.0, 12.0, 12.0], "low": [8.0, 8.0, 8.0],
                          "close": [8.0, 12.0, 10.0]})
    transformed = compiled.frame_transform(frame)
    assert transformed is not None
    assert transformed[FACTOR_COLUMN].to_list() == [1.0, 0.0, 0.5]


def test_fixed_reversal_blend_contains_intraday_and_close_position_reversal() -> None:
    _load_factors()
    intraday = get_factor("uf_intraday_reversal")
    close_reversal = get_factor(CLOSE_REVERSAL_ID)
    blend = get_factor(BLEND_ID)
    assert intraday is not None and close_reversal is not None
    assert blend is not None
    assert blend.kind == "composite"
    assert dict(blend.components) == {"uf_intraday_reversal": 0.5, CLOSE_REVERSAL_ID: 0.5}
    assert intraday.dependencies == frozenset({"open", "close"})
    assert close_reversal.dependencies == frozenset({"high", "low", "close"})

    materialized = materialize_scoring_columns(
        pl.DataFrame({"symbol": ["A", "B", "C"], "date": [date(2026, 1, 5)] * 3,
                      "open": [10.0, 10.0, 10.0], "high": [12.0, 12.0, 12.0],
                      "low": [8.0, 8.0, 8.0], "close": [8.0, 12.0, 10.0]}),
        ["uf_intraday_reversal", CLOSE_REVERSAL_ID, BLEND_ID],
    )
    assert all(abs(a - b) < 1e-12 for a, b in zip(materialized["uf_intraday_reversal"].to_list(), [0.2, -0.2, 0.0], strict=True))
    assert all(abs(a - b) < 1e-12 for a, b in zip(materialized[CLOSE_REVERSAL_ID].to_list(), [1.0, 0.0, 0.5], strict=True))
    assert materialized[BLEND_ID].is_not_null().all()


def test_r363_is_matched_against_the_five_session_quality_control() -> None:
    _load_factors()
    engine = StrategyEngine(strategy_dirs=[STRATEGY_DIR])
    candidate, control = engine.get(CANDIDATE_ID), engine.get(CONTROL_ID)
    assert candidate.meta["scoring"] == {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        BLEND_ID: 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    }
    assert control.max_hold_days == candidate.max_hold_days == 5
    assert control.stop_loss == candidate.stop_loss == -0.20
    assert control.basic_filter == candidate.basic_filter
    assert candidate.meta["scoring"][BLEND_ID] == 0.20


def test_r363_preregistration_fixes_t1_weak_filter_and_no_sweep() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    assert prereg["round"] == "R363"
    assert prereg["rule"]["factor"] == BLEND_ID
    assert prereg["rule"]["regime_filter"] == {"alignment": "T-1", "states": ["weak"]}
    assert prereg["rule"]["holding_sessions"] == 5
    assert prereg["parameter_sweep"] is False
    assert prereg["decision"]["selected"] is None
    assert prereg["decision"]["production_enabled"] is False
    assert prereg["primary_window"]["cost_multipliers"] == [2, 3]
    assert len(prereg["primary_window"]["phase_starts"]) == 6


def test_r363_preflight_and_backtest_share_frozen_input_manifest() -> None:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    preflight = _load_module(EVIDENCE / "run_r363_weak_reversal_blend_preflight.py", "r363_preflight_contract")
    backtest = _load_module(RUNNER_PATH, "r363_backtest_contract")
    assert preflight.PRE_REG == backtest.PRE_REG == PREREG_PATH
    assert preflight.OUTPUT == backtest.PREFLIGHT
    assert preflight.input_fingerprints() == backtest.input_fingerprints()
    assert prereg["frozen_inputs"]["backtest_runner_sha256"]
    assert prereg["frozen_inputs"]["preflight_runner_sha256"]
    assert prereg["frozen_inputs"]["test_sha256"]
    assert prereg["frozen_inputs"]["test_isolation_sha256"]
