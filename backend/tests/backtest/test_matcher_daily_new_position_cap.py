from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from app.backtest.engine import BacktestEngine, MatcherConfig
from app.backtest.matrix import (
    build_market_data_matrix,
    build_market_matrix_from_signals,
    make_signal_matrix,
)
from app.tickflow.repository import DataStore, KlineRepository


def test_matcher_daily_new_position_cap_limits_same_day_entries(tmp_path):
    panel = pl.DataFrame(
        {
            "symbol": ["000001.SZ", "000002.SZ", "000003.SZ"] * 3,
            "date": [date(2024, 1, 2)] * 3 + [date(2024, 1, 3)] * 3 + [date(2024, 1, 4)] * 3,
            "open": [10.0] * 9,
            "high": [10.5] * 9,
            "low": [9.5] * 9,
            "close": [10.0] * 9,
            "volume": [1000.0] * 9,
            "amount": [100000.0] * 9,
            "turnover_rate": [1.0] * 9,
        }
    )
    market = build_market_data_matrix(panel, field_columns={"amount", "turnover_rate"})
    entry = np.zeros(market.shape, dtype=bool)
    entry[0, :] = True
    signals = make_signal_matrix(
        market.shape,
        entry=entry.astype(np.uint8),
        exit=np.zeros(market.shape, dtype=np.uint8),
        score=np.ones(market.shape, dtype=np.float32),
        entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
        exit_signal_code=np.full(market.shape, -1, dtype=np.int16),
    )
    matrix = build_market_matrix_from_signals(market, signals)
    config = MatcherConfig(
        matching="open_t+1",
        max_positions=3,
        max_new_positions_per_day=1,
        initial_capital=1_000_000.0,
    )
    result = BacktestEngine(KlineRepository(DataStore(tmp_path))).simulate_market_matrix(
        matrix, config
    )
    assert len(result.trades) == 1
