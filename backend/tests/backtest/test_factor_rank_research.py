from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
from fastapi import HTTPException

from app.api import screener as screener_api
from app.api import strategy as strategy_api
from app.backtest.matrix import build_market_data_matrix, validate_signal_matrix
from app.backtest.optimizer import expand_param_grid
from app.backtest.strategy import StrategyDependencyResolver
from app.strategy.engine import StrategyEngine

STRATEGY_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "strategy"
    / "research"
    / "factor_rank_research.py"
)


def _market():
    panel = pl.DataFrame({
        "symbol": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"] * 2,
        "date": [date(2024, 1, 2)] * 4 + [date(2024, 1, 3)] * 4,
        "open": [10.0] * 8,
        "high": [10.5] * 8,
        "low": [9.5] * 8,
        "close": [10.0] * 8,
        "volume": [1_000.0] * 8,
        "amount": [1.0, 2.0, 3.0, 4.0, 4.0, 3.0, 2.0, 1.0],
        "turnover_rate": [4.0, 3.0, 2.0, 1.0, 1.0, 2.0, 3.0, 4.0],
    })
    return build_market_data_matrix(
        panel,
        field_columns={"amount", "turnover_rate"},
    )


def test_strategy_loads_as_builtin_matrix_native_and_grid_params_validate():
    strategy = StrategyEngine._load_file(STRATEGY_PATH)

    assert strategy.meta["id"] == "factor_rank_research"
    assert strategy.meta["research_only"] is True
    assert strategy.execution_backend == "matrix_native"
    assert strategy.matrix_strategy is not None
    assert strategy.meta["scoring"] == {}
    combos = expand_param_grid(
        strategy.meta["params"],
        {
            "entry_score": [50.0, 75.0],
            "exit_score": [20.0],
            "top_rank": [1, 2],
        },
    )
    assert len(combos) == 4
    assert strategy.matrix_strategy.required_warmup_bars({}) == 60
    assert {"amount", "turnover_rate", "close"}.issubset(
        strategy.matrix_strategy.required_fields()
    )


def test_research_template_is_hidden_from_ordinary_strategy_apis(tmp_path):
    engine = StrategyEngine(strategy_dirs=[STRATEGY_PATH.parent])
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(strategy_engine=engine, repo=repo))
    )

    screener_payload = screener_api.strategies(request)
    strategy_payload = strategy_api.list_strategies(request)

    assert engine.has("factor_rank_research")
    assert "factor_rank_research" in {
        item["id"] for item in engine.list_strategies(include_research=True)
    }
    assert "factor_rank_research" not in {
        item["id"] for item in screener_payload["presets"]
    }
    assert "factor_rank_research" not in {
        item["id"] for item in strategy_payload["strategies"]
    }

    with pytest.raises(HTTPException) as screener_error:
        screener_api.run_preset(
            screener_api.PresetRequest(
                strategy_id="factor_rank_research",
                as_of=date(2024, 1, 2),
            ),
            request,
        )
    assert screener_error.value.status_code == 404

    with pytest.raises(HTTPException) as strategy_error:
        strategy_api.run_strategy(
            strategy_api.RunRequest(
                strategy_id="factor_rank_research",
                as_of=date(2024, 1, 2),
            ),
            request,
        )
    assert strategy_error.value.status_code == 404


def test_dependency_resolver_includes_parameter_scoring_fields():
    strategy = StrategyEngine._load_file(STRATEGY_PATH)

    plan = StrategyDependencyResolver().resolve(
        strategy,
        params={"scoring": {"amount": 1.0, "ma20_bias": 1.0}},
        basic_filter={"enabled": False},
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
    )

    assert {"amount", "close"}.issubset(plan.base_columns)
    assert plan.indicator_columns == frozenset()
    assert {"amount", "close"}.issubset(plan.matrix_columns)


def test_strategy_uses_controlled_scoring_directions_thresholds_and_top_rank():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()

    signals = strategy.compute_signals(
        market,
        {
            "scoring": {"amount": 1.0, "turnover_rate": 1.0},
            "directions": {"amount": "high", "turnover_rate": "low"},
            "entry_score": 60.0,
            "exit_score": 25.0,
            "top_rank": 1,
        },
    )

    validate_signal_matrix(signals, market.shape)
    assert signals.entry.sum(axis=1).tolist() == [1, 1]
    assert signals.entry.tolist() == [[0, 0, 0, 1], [1, 0, 0, 0]]
    assert signals.exit.tolist() == [[1, 0, 0, 0], [0, 0, 0, 1]]
    assert signals.entry_signal_ids == ("signal_factor_rank_entry",)
    assert signals.exit_signal_ids == ("signal_factor_rank_exit",)
    assert not signals.score.flags.writeable


def test_optional_industry_cap_limits_daily_entries():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()
    params = {
        "scoring": {"amount": 1.0},
        "directions": {"amount": "high"},
        "entry_score": 0.0,
        "exit_score": 0.0,
        "top_rank": 4,
        "industry_by_symbol": {
            "000001.SZ": "银行",
            "000002.SZ": "银行",
            "000003.SZ": "银行",
            "000004.SZ": "公用事业",
        },
        "max_industry_positions": 1,
    }
    signals = strategy.compute_signals(market, params)
    assert signals.entry.sum(axis=1).tolist() == [2, 2]


def test_strategy_direction_changes_score_without_dynamic_formula_execution():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()

    high = strategy.compute_signals(
        market,
        {
            "scoring": {"amount": 1.0},
            "directions": {"amount": "high"},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 4,
        },
    )
    low = strategy.compute_signals(
        market,
        {
            "scoring": {"amount": 1.0},
            "directions": {"amount": "low"},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 4,
        },
    )

    np.testing.assert_allclose(high.score + low.score, 100.0)
    with pytest.raises(ValueError, match="unsupported matrix feature"):
        strategy.compute_signals(
            market,
            {
                "scoring": {"__import__('os').system('bad')": 1.0},
                "entry_score": 50.0,
                "exit_score": 20.0,
                "top_rank": 1,
            },
        )


def test_strategy_supports_eight_factor_watch_set_members():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()
    scoring = {
        "open": 0.125,
        "high": 0.125,
        "low": 0.125,
        "close": 0.125,
        "volume": 0.125,
        "amount": 0.125,
        "turnover_rate": 0.125,
        "change_pct": 0.125,
    }

    signals = strategy.compute_signals(
        market,
        {
            "scoring": scoring,
            "directions": {name: "high" for name in scoring},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 4,
        },
    )

    validate_signal_matrix(signals, market.shape)
    assert signals.entry.shape == market.shape


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"scoring": {}}, "non-empty scoring"),
        (
            {"scoring": {f"factor_{index}": 1.0 for index in range(9)}},
            "at most 8 factors",
        ),
        (
            {
                "scoring": {"amount": 1.0},
                "directions": {"turnover_rate": "low"},
            },
            "absent from scoring",
        ),
        (
            {
                "scoring": {"amount": 1.0},
                "entry_score": 20.0,
                "exit_score": 30.0,
            },
            "exit_score must not exceed",
        ),
    ],
)
def test_strategy_rejects_uncontrolled_or_invalid_research_params(params, message):
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy

    with pytest.raises(ValueError, match=message):
        strategy.compute_signals(_market(), params)


def test_dynamic_scoring_switches_weights_by_lagged_state():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()
    params = {
        "entry_score": 0.0,
        "exit_score": 0.0,
        "top_rank": 4,
        "dynamic_scoring": {
            "2024-01-02": {
                "scoring": {"amount": 1.0},
                "directions": {"amount": "high"},
            },
            "2024-01-03": {
                "scoring": {"turnover_rate": 1.0},
                "directions": {"turnover_rate": "low"},
            },
        },
    }

    signals = strategy.compute_signals(market, params)

    validate_signal_matrix(signals, market.shape)
    np.testing.assert_allclose(signals.score[0], [0.0, 100.0 / 3.0, 200.0 / 3.0, 100.0])
    np.testing.assert_allclose(signals.score[1], [100.0, 200.0 / 3.0, 100.0 / 3.0, 0.0])
    assert not np.allclose(signals.score[0], signals.score[1])


def test_dynamic_scoring_fails_closed_when_state_missing():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()
    params = {
        "dynamic_scoring": {
            "2024-01-02": {
                "scoring": {"amount": 1.0},
                "directions": {"amount": "high"},
            },
        },
    }

    with pytest.raises(ValueError, match="dynamic scoring missing date"):
        strategy.compute_signals(market, params)


def test_dynamic_scoring_matches_per_date_static_scoring():
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    market = _market()
    dynamic = strategy.compute_signals(
        market,
        {
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 4,
            "dynamic_scoring": {
                "2024-01-02": {"scoring": {"amount": 1.0}, "directions": {"amount": "high"}},
                "2024-01-03": {"scoring": {"turnover_rate": 1.0}, "directions": {"turnover_rate": "low"}},
            },
        },
    )
    first = strategy.compute_signals(
        market,
        {"scoring": {"amount": 1.0}, "directions": {"amount": "high"}, "entry_score": 0.0, "exit_score": 0.0, "top_rank": 4},
    )
    second = strategy.compute_signals(
        market,
        {"scoring": {"turnover_rate": 1.0}, "directions": {"turnover_rate": "low"}, "entry_score": 0.0, "exit_score": 0.0, "top_rank": 4},
    )
    np.testing.assert_allclose(dynamic.score[0], first.score[0])
    np.testing.assert_allclose(dynamic.score[1], second.score[1])


def test_matrix_pipeline_neutralization_changes_entry_selection() -> None:
    from app.backtest.matrix import MatrixPipelineConfig, MatrixStrategyPipeline

    panel = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "open": [10.0] * 4,
            "high": [10.5] * 4,
            "low": [9.5] * 4,
            "close": [10.0] * 4,
            "volume": [1_000.0] * 4,
            "amount": [1.0, 2.0, 10.0, 11.0],
            "turnover_rate": [1.0] * 4,
            "industry_l1": ["bank", "bank", "tech", "tech"],
            "log_float_mv": [1.0, 2.0, 3.0, 4.0],
        }
    )
    market = build_market_data_matrix(
        panel, field_columns={"amount", "turnover_rate", "industry_l1", "log_float_mv"}
    )
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    base = MatrixStrategyPipeline().run(
        strategy,
        market,
        {
            "scoring": {"amount": 1.0},
            "directions": {"amount": "high"},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 1,
        },
        MatrixPipelineConfig(
            basic_filter={"enabled": False},
            scoring={"amount": 1.0},
            order_by="score",
            descending=True,
            scoring_directions={"amount": "high"},
        ),
    )
    neutral = MatrixStrategyPipeline().run(
        strategy,
        market,
        {
            "scoring": {"amount": 1.0},
            "directions": {"amount": "high"},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 2,
        },
        MatrixPipelineConfig(
            basic_filter={"enabled": False},
            scoring={"amount": 1.0},
            order_by="score",
            descending=True,
            scoring_directions={"amount": "high"},
            neutralize={
                "industry": "industry_l1",
                "styles": ["log_float_mv"],
                "shrink": 1.0,
            },
            industry_by_symbol={
                "A": "bank",
                "B": "bank",
                "C": "tech",
                "D": "tech",
            },
        ),
    )
    assert base.entry.sum() == 1
    assert neutral.entry.sum() == 2
    assert neutral.entry[0].tolist() != base.entry[0].tolist()


def test_matrix_pipeline_supports_industry_only_neutralization() -> None:
    from app.backtest.matrix import MatrixPipelineConfig, MatrixStrategyPipeline

    panel = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "open": [10.0] * 4,
            "high": [10.5] * 4,
            "low": [9.5] * 4,
            "close": [10.0] * 4,
            "volume": [1_000.0] * 4,
            "amount": [1.0, 2.0, 10.0, 11.0],
            "turnover_rate": [1.0] * 4,
            "industry_l1": ["bank", "bank", "tech", "tech"],
        }
    )
    market = build_market_data_matrix(
        panel, field_columns={"amount", "turnover_rate", "industry_l1"}
    )
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    result = MatrixStrategyPipeline().run(
        strategy,
        market,
        {
            "scoring": {"amount": 1.0},
            "directions": {"amount": "high"},
            "entry_score": 0.0,
            "exit_score": 0.0,
            "top_rank": 2,
        },
        MatrixPipelineConfig(
            basic_filter={"enabled": False},
            scoring={"amount": 1.0},
            order_by="score",
            descending=True,
            scoring_directions={"amount": "high"},
            neutralize={"industry": "industry_l1", "styles": [], "shrink": 1.0},
            industry_by_symbol={
                "A": "bank",
                "B": "bank",
                "C": "tech",
                "D": "tech",
            },
        ),
    )

    assert result.entry.sum() == 2


def test_matrix_pipeline_neutralizes_parameter_scoring_when_meta_scoring_is_empty() -> None:
    """Research strategies carry scoring in params; neutralization must see it.

    ``factor_rank_research`` intentionally keeps ``META["scoring"]`` empty and
    supplies the factor set through ``params["scoring"]``. The formal backtest
    path therefore constructs ``MatrixPipelineConfig(scoring={})``. A
    neutralization pass that only reads the config mapping silently becomes a
    no-op even though the override was accepted.
    """
    from app.backtest.matrix import MatrixPipelineConfig, MatrixStrategyPipeline

    panel = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "open": [10.0] * 4,
            "high": [10.5] * 4,
            "low": [9.5] * 4,
            "close": [10.0] * 4,
            "volume": [1_000.0] * 4,
            "amount": [1.0, 2.0, 10.0, 11.0],
            "turnover_rate": [1.0] * 4,
            "industry_l1": ["bank", "bank", "tech", "tech"],
            "log_float_mv": [1.0, 2.0, 3.0, 4.0],
        }
    )
    market = build_market_data_matrix(
        panel, field_columns={"amount", "turnover_rate", "industry_l1", "log_float_mv"}
    )
    strategy = StrategyEngine._load_file(STRATEGY_PATH).matrix_strategy
    params = {
        "scoring": {"amount": 1.0},
        "directions": {"amount": "high"},
        "entry_score": 0.0,
        "exit_score": 0.0,
        "top_rank": 2,
    }
    base_config = MatrixPipelineConfig(
        basic_filter={"enabled": False},
        scoring={},
        order_by="score",
        descending=True,
        scoring_directions={"amount": "high"},
    )
    neutral_config = MatrixPipelineConfig(
        basic_filter={"enabled": False},
        scoring={},
        order_by="score",
        descending=True,
        scoring_directions={"amount": "high"},
        neutralize={
            "industry": "industry_l1",
            "styles": ["log_float_mv"],
            "shrink": 1.0,
        },
        industry_by_symbol={
            "A": "bank",
            "B": "bank",
            "C": "tech",
            "D": "tech",
        },
    )

    base = MatrixStrategyPipeline().run(strategy, market, params, base_config)
    neutral = MatrixStrategyPipeline().run(strategy, market, params, neutral_config)

    assert neutral.entry.tolist() != base.entry.tolist()
    assert neutral.score.tolist() != base.score.tolist()
