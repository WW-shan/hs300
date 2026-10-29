"""Contract tests for the registered R273 macro-gate exit signals."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.strategy import StrategyDependencyResolver
from app.indicators import pipeline
from app.factors.store import load_into_registry
from app.strategy import config as strategy_config
from app.strategy import custom_signals
from app.strategy.signal_seeds import SEED_SIGNALS
from app.strategy.engine import StrategyEngine

ROOT = Path(__file__).resolve().parents[2]

SIGNAL_IDS = (
    "r273_macro_exit_erp",
    "r273_macro_exit_credit",
    "r273_macro_exit_term",
)


def _signals() -> list[dict]:
    fixture_dir = Path(__file__).resolve().parent / "fixtures" / "custom_signals"
    signals = [
        json.loads((fixture_dir / f"{signal_id}.json").read_text(encoding="utf-8"))
        for signal_id in SIGNAL_IDS
    ]
    for signal in signals:
        custom_signals.validate(signal)
    return signals


def test_r273_exit_signals_trigger_independently_and_or_compose():
    signals = _signals()
    expressions = custom_signals.build_expressions(signals)
    frame = pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "date": [1, 2, 3, 4],
            "ext_alt_hs300_equity_risk_premium": [5.4, 5.6, 5.6, 5.6],
            "ext_alt_hs300_credit_spread_5y_low": [-0.2, -0.9, -0.2, -0.2],
            "ext_alt_hs300_term_spread_10y_2y": [0.4, 0.4, 0.2, 0.4],
        }
    )

    evaluated = custom_signals.inject(frame, expressions).select(sorted(expressions))
    rows = evaluated.to_dicts()
    assert [sum(bool(value) for value in row.values()) for row in rows] == [1, 1, 1, 0]
    assert [next(name for name, hit in row.items() if hit) for row in rows[:3]] == [
        "csg_r273_macro_exit_erp",
        "csg_r273_macro_exit_credit",
        "csg_r273_macro_exit_term",
    ]


def test_r273_strategy_resolves_signal_and_factor_dependencies(monkeypatch):
    data_dir = ROOT / "data"
    load_into_registry(data_dir)
    definitions = [*SEED_SIGNALS, *_signals()]
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", custom_signals.build_expressions(definitions))
    prev_fields = custom_signals.prev_day_fields(definitions)
    monkeypatch.setattr(
        pipeline,
        "_custom_signal_exprs_today",
        custom_signals.build_expressions_prev(definitions, prev_fields),
    )
    monkeypatch.setattr(pipeline, "_custom_prev_fields_cache", prev_fields)
    engine = StrategyEngine(
        strategy_dirs=[data_dir / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(data_dir, sid),
    )
    strategy = engine.get("ai_hs300_macro_gate_quality_r273_gate_exit")
    plan = StrategyDependencyResolver().resolve(
        strategy,
        params={},
        basic_filter=strategy.basic_filter,
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
        asset_type="stock",
    )

    assert strategy.max_hold_days == 35
    assert set(strategy.exit_signals) == {f"csg_{signal_id}" for signal_id in SIGNAL_IDS}
    assert set(strategy.exit_signals) <= set(plan.signal_columns)
    assert {
        "ext_alt_hs300_equity_risk_premium",
        "ext_alt_hs300_credit_spread_5y_low",
        "ext_alt_hs300_term_spread_10y_2y",
    } <= set(plan.ext_columns)


def test_r273_macro_exit_uses_system_next_session_open_fill():
    panel = pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "date": [1, 2, 3, 4],
            "open": [9.0, 10.0, 12.0, 14.0],
            "high": [9.0, 10.0, 12.0, 14.0],
            "low": [9.0, 10.0, 12.0, 14.0],
            "close": [9.0, 10.0, 12.0, 14.0],
            "signal_entry": [True, False, False, False],
            "csg_r273_macro_exit_erp": [False, False, True, False],
            "csg_r273_macro_exit_credit": [False, False, False, False],
            "csg_r273_macro_exit_term": [False, False, False, False],
            "name": ["A"] * 4,
        }
    )
    engine = BacktestEngine(repo=None)
    result = engine.simulate_portfolio(
        panel,
        panel["signal_entry"],
        panel["csg_r273_macro_exit_erp"]
        | panel["csg_r273_macro_exit_credit"]
        | panel["csg_r273_macro_exit_term"],
        MatcherConfig(matching="open_t+1", fees_pct=0, slippage_bps=0, max_positions=1),
    )

    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "signal"
    assert result.trades[0].exit_price == 14.0
