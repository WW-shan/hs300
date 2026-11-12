from __future__ import annotations

import json
from datetime import date, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import polars as pl

from app.backtest.engine import SimResult
from app.backtest.regime_alignment import build_regime_filter_mask
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.dsl import FACTOR_COLUMN, compile_formula
from app.factors.ext_factors import ensure_synced
from app.factors.registry import get_factor, register_factor, unregister_factor
from app.factors.store import to_spec
from app.services import regime_builder
from app.strategy.engine import StrategyDef, StrategyEngine
from app.strategy.scoring import materialize_scoring_columns

ROOT = Path(__file__).resolve().parents[2]
FACTOR_PATH = ROOT / "data/user_data/custom_factors/uf_intraday_reversal.json"
STRATEGY_DIR = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r361_strategies"
CANDIDATE_ID = "ai_hs300_industry_relative_strength_intraday_reversal_weak_h20_sl20"
CONTROL_ID = "ai_hs300_industry_relative_strength_quality_weak_h20_sl20"
FACTOR_ID = "uf_intraday_reversal"
REGIME_FILTER = {"states": ["weak"]}


class _StrategyEngineStub:
    def __init__(self, strategy):
        self.strategy = strategy

    def get(self, strategy_id: str):
        return self.strategy


class _EngineStub:
    def __init__(self, panel: pl.DataFrame, data_dir: Path):
        self.panel = panel
        self.repo = type("Repo", (), {"store": type("Store", (), {"data_dir": data_dir})()})()
        self.sim_matrix = None

    def load_panel(self, symbols, start, end, columns=None, asset_type="stock"):
        return self.panel

    def load_panel_for_backtest(self, symbols, start, end, feature_plan, asset_type="stock"):
        return self.panel

    def simulate_market_matrix(self, matrix, config, *args, **kwargs):
        self.sim_matrix = matrix
        return SimResult(
            equity_curve=[{"date": "2026-01-05", "value": config.initial_capital}],
            drawdown_curve=[],
            trades=[],
            per_symbol_stats=[],
            stats={"total_return": 0.0},
        )


def _strategy_definition() -> StrategyDef:
    return StrategyDef(
        meta={"id": "r361_regime_alignment", "name": "R361 test", "scoring": {}, "params": [], "limit": 10},
        basic_filter={"enabled": False},
        entry_signals=[],
        exit_signals=["signal_limit_up"],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=lambda _df, _params: pl.lit(True),
        filter_history_fn=None,
        lookback_days=1,
        source="custom",
        file_path=None,
    )


def _register_r361_factor() -> bool:
    ensure_synced(ROOT / "data")
    spec = to_spec(json.loads(FACTOR_PATH.read_text(encoding="utf-8")))
    existing = get_factor(FACTOR_ID)
    if existing is None:
        register_factor(spec)
        return True
    assert existing == spec
    return False


def test_intraday_reversal_factor_is_negative_intraday_return() -> None:
    registered_here = _register_r361_factor()
    try:
        definition = json.loads(FACTOR_PATH.read_text(encoding="utf-8"))
        compiled = compile_formula(definition["formula"])
        assert compiled.ok
        assert compiled.dependencies == {"open", "close"}

        panel = pl.DataFrame(
            {
                "symbol": ["A", "B", "C"],
                "date": [date(2026, 1, 5)] * 3,
                "open": [10.0, 10.0, 10.0],
                "close": [9.0, 11.0, 10.0],
            }
        )
        transformed = compiled.frame_transform(panel)
        assert transformed is not None
        expected = [0.1, -0.1, 0.0]
        actual = transformed[FACTOR_COLUMN].to_list()
        assert all(abs(value - target) < 1e-12 for value, target in zip(actual, expected, strict=True))
        materialized = materialize_scoring_columns(panel, [FACTOR_ID])[FACTOR_ID].to_list()
        assert all(abs(value - target) < 1e-12 for value, target in zip(materialized, expected, strict=True))
    finally:
        if registered_here:
            unregister_factor(FACTOR_ID)


def test_candidate_replaces_only_twenty_percent_quality_value_weight() -> None:
    registered_here = _register_r361_factor()
    try:
        engine = StrategyEngine(strategy_dirs=[STRATEGY_DIR])
        candidate = engine.get(CANDIDATE_ID)
        control = engine.get(CONTROL_ID)
        baseline = {
            "ext_alt_hs300_stock_rel_industry_20d": 0.35,
            "ext_alt_hs300_industry_mom_20d": 0.15,
            "uf_quality_value_defensive": 0.30,
            "uf_cash_quality": 0.20,
        }
        expected_candidate = {
            "ext_alt_hs300_stock_rel_industry_20d": 0.35,
            "ext_alt_hs300_industry_mom_20d": 0.15,
            FACTOR_ID: 0.20,
            "uf_quality_value_defensive": 0.10,
            "uf_cash_quality": 0.20,
        }

        assert candidate.meta["scoring"] == expected_candidate
        assert control.meta["scoring"] == baseline
        assert candidate.basic_filter == control.basic_filter
        assert candidate.stop_loss == control.stop_loss == -0.20
        assert candidate.max_hold_days == control.max_hold_days == 20
        assert FACTOR_ID in candidate.required_features
        assert FACTOR_ID not in control.required_features
        assert abs(sum(candidate.meta["scoring"].values()) - 1.0) < 1e-12
    finally:
        if registered_here:
            unregister_factor(FACTOR_ID)


def test_weak_regime_filter_uses_previous_trading_session() -> None:
    labels = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    regime_by_date = {
        "2026-01-02": {"state": "strong", "score": 80},
        "2026-01-05": {"state": "strong", "score": 80},
        "2026-01-06": {"state": "weak", "score": 25},
        "2026-01-07": {"state": "weak", "score": 28},
    }

    actual = build_regime_filter_mask(
        labels,
        REGIME_FILTER,
        regime_by_date,
        required_start=date(2026, 1, 5),
        required_end=date(2026, 1, 8),
    )

    assert actual is not None
    assert actual.tolist() == [True, False, False, True, True]


def test_strategy_backtest_applies_weak_regime_t1_filter_to_entries_only(tmp_path: Path) -> None:
    start = date(2026, 1, 5)
    dates = [start + timedelta(days=offset) for offset in range(-3, 2)]
    panel = pl.DataFrame([
        {
            "symbol": "A",
            "name": "A",
            "date": day,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1000.0,
            "amount": 1000.0,
            "signal_limit_up": True,
            "signal_limit_down": False,
        }
        for day in dates
    ]).sort(["symbol", "date"])
    regime_builder.upsert_regime_history(tmp_path, pl.DataFrame({
        "date": dates[:-1],
        "state": ["strong", "weak", "strong", "weak"],
        "score": [80, 10, 80, 20],
    }))
    engine = _EngineStub(panel, tmp_path)
    service = StrategyBacktestService(engine, _StrategyEngineStub(_strategy_definition()))

    result = service.run(StrategyBacktestConfig(
        strategy_id="r361_regime_alignment",
        symbols=None,
        start=start,
        end=dates[-1],
        matching="close_t",
        mode="position",
        regime_filter=REGIME_FILTER,
    ))

    assert result.error is None
    assert engine.sim_matrix is not None
    assert engine.sim_matrix.entry[:, 0].tolist() == [0, 1]
    assert engine.sim_matrix.exit[:, 0].tolist() == [1, 1]


def test_backtest_config_uses_t1_weak_regime_and_cost_multiplier() -> None:
    runner_path = STRATEGY_DIR.parent / "run_r361_intraday_reversal.py"
    spec = spec_from_file_location("r361_runner_contract", runner_path)
    assert spec is not None and spec.loader is not None
    runner = module_from_spec(spec)
    spec.loader.exec_module(runner)

    config = runner.make_backtest_config(CANDIDATE_ID, date(2022, 5, 5), 2)

    assert config.regime_filter == REGIME_FILTER
    assert config.matching == "open_t+1"
    assert config.max_exposure_pct == 1.0
    assert config.max_positions == 10
    assert config.fees_pct == 0.0004
    assert config.commission_pct == 0.0004
    assert config.stamp_tax_pct == 0.0005
    assert config.slippage_bps == 10.0


def test_preflight_regime_mask_rejects_signal_day_not_prior_day(tmp_path: Path) -> None:
    preflight_path = STRATEGY_DIR.parent / "run_r361_intraday_reversal_preflight.py"
    spec = spec_from_file_location("r361_preflight_mask_contract", preflight_path)
    assert spec is not None and spec.loader is not None
    preflight = module_from_spec(spec)
    spec.loader.exec_module(preflight)

    dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    regime = pl.DataFrame({
        "date": [date.fromisoformat(day) for day in dates],
        "state": ["strong", "weak", "strong", "weak"],
        "score": [80, 10, 80, 20],
    })

    mask = preflight.weak_regime_entry_mask(
        dates,
        regime,
        start=date(2026, 1, 5),
        end=date(2026, 1, 7),
    )

    assert mask == [True, False, True, False]
