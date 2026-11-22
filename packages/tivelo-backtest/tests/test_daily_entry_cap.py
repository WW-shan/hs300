from __future__ import annotations

from datetime import date

import polars as pl

from tivelo_backtest.legacy.simulator import LegacySimulator, MatcherConfig, SimulationOptions
from tivelo_backtest.research.matrix import build_market_matrix

DAYS = [date(2025, 1, day) for day in (2, 3, 6)]


def _matrix() -> object:
    frame = pl.DataFrame([
        {
            "symbol": symbol,
            "date": day,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100_000.0,
            "score": 1.0,
            "signal_limit_up": False,
            "signal_limit_down": False,
        }
        for symbol in ("A", "B", "C")
        for day in DAYS
    ])
    entries = (frame["date"] == DAYS[0])
    exits = pl.Series([False] * frame.height)
    return build_market_matrix(frame, entries, exits, entry_delay_bars=1, exit_delay_bars=1)


def test_daily_entry_cap_limits_new_positions() -> None:
    config = MatcherConfig(
        matching="open_t+1",
        commission_pct=0,
        stamp_tax_pct=0,
        slippage_bps=0,
        max_positions=3,
        max_new_positions_per_day=1,
        initial_capital=1_000_000,
    )
    result = LegacySimulator().simulate_market_matrix(
        _matrix(), config, None, None, SimulationOptions(include_monte_carlo=False)
    )
    assert len(result.trades) == 1
