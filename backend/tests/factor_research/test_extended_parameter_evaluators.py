from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.factor_research.extended_parameter_evaluators import (
    EXTENDED_EVALUATORS,
    etf_share_change,
    event_decay_factor,
    industry_window_frame,
    market_change_frame,
    market_momentum_frame,
    market_relative_momentum_frame,
    market_skew_frame,
    rank_score_series,
    rolling_zscore_frame,
)
from app.factor_research.parameters import load_parameter_registry


def _dates(count: int, start: date = date(2024, 1, 2)) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def test_event_decay_starts_next_trading_day_and_sums_events() -> None:
    calendar = pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "date": _dates(4),
        }
    )
    events = pl.DataFrame(
        {
            "symbol": ["A", "A"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "value": [1.0, 2.0],
        }
    )

    out = event_decay_factor(
        events,
        calendar,
        "value",
        3,
        "factor_value",
    ).sort("date")

    assert out["date"].to_list() == _dates(4)[1:]
    assert out["factor_value"].to_list() == pytest.approx([3.0, 2.0, 1.0])


def test_event_decay_applies_multiplier_and_symbol_isolation() -> None:
    calendar = pl.DataFrame(
        {
            "symbol": ["A"] * 3 + ["B"] * 3,
            "date": _dates(3) * 2,
        }
    )
    events = pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "value": [2.0, 5.0],
        }
    )

    out = event_decay_factor(
        events,
        calendar,
        "value",
        2,
        "factor_value",
        multiplier=-1.0,
    ).sort(["symbol", "date"])

    assert out.filter(pl.col("symbol") == "A")["factor_value"].to_list() == [-2.0, -1.0]
    assert out.filter(pl.col("symbol") == "B")["factor_value"].to_list() == [-5.0, -2.5]


def test_event_decay_can_fill_complete_calendar_with_zeroes() -> None:
    calendar = pl.DataFrame({
        "symbol": ["A", "A", "B"],
        "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2)],
    })
    events = pl.DataFrame({
        "symbol": ["A"],
        "date": [date(2024, 1, 2)],
        "value": [1.0],
    })

    out = event_decay_factor(
        events,
        calendar,
        "value",
        2,
        "factor_value",
        fill_zero=True,
    ).sort(["symbol", "date"])

    assert out["factor_value"].to_list() == [0.0, 1.0, 0.0]


def test_insider_event_keeps_non_event_stocks_in_the_cross_section() -> None:
    dates = _dates(2)
    calendar = pl.DataFrame(
        {
            "symbol": ["A", "A", "B", "B"],
            "date": [*dates, *dates],
        }
    )
    context = {
        "calendar": calendar,
        "events": {
            "insider": pl.DataFrame(
                {
                    "symbol": ["A"],
                    "date": [dates[0]],
                    "buy": [1.0],
                    "sell": [0.0],
                }
            )
        },
    }

    out, column, _ = EXTENDED_EVALUATORS["insider_buy_event"](context, 2)

    assert out.height == 4
    assert out.filter(pl.col("symbol") == "B")[column].to_list() == [0.0, 0.0]


def test_industry_momentum_rps_breadth_and_relative_strength() -> None:
    rows: list[dict[str, object]] = []
    closes = {
        "A1": [10.0, 11.0, 12.0, 13.0],
        "A2": [10.0, 10.0, 10.0, 10.0],
        "B1": [10.0, 9.0, 8.0, 7.0],
        "B2": [10.0, 9.0, 8.2, 7.5],
    }
    for symbol, values in closes.items():
        industry = "A" if symbol.startswith("A") else "B"
        for day, close in zip(_dates(4), values, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "close": close,
                    "industry_l1": industry,
                }
            )
    prices = pl.DataFrame(rows).sort(["symbol", "date"])

    momentum, mom_col, _ = industry_window_frame(prices, "momentum", 2)
    rps, rps_col, _ = industry_window_frame(prices, "rps", 2)
    breadth, breadth_col, _ = industry_window_frame(prices, "breadth", 2)
    relative, rel_col, _ = industry_window_frame(prices, "stock_relative", 2)

    latest = _dates(4)[-1]
    latest_momentum = momentum.filter(pl.col("date") == latest)
    latest_rps = rps.filter(pl.col("date") == latest)
    latest_breadth = breadth.filter(pl.col("date") == latest)
    latest_relative = relative.filter(pl.col("date") == latest)

    assert latest_momentum.filter(pl.col("industry_l1") == "A")[mom_col].first() > 0
    assert latest_momentum.filter(pl.col("industry_l1") == "B")[mom_col].first() < 0
    assert latest_rps.filter(pl.col("industry_l1") == "A")[rps_col].first() == 1.0
    assert latest_rps.filter(pl.col("industry_l1") == "B")[rps_col].first() == 0.5
    assert latest_breadth.filter(pl.col("industry_l1") == "A")[breadth_col].first() == 0.5
    assert latest_breadth.filter(pl.col("industry_l1") == "B")[breadth_col].first() == 0.0
    assert latest_relative[rel_col].n_unique() == 4


def test_market_momentum_and_relative_momentum() -> None:
    dates = _dates(4)
    left = pl.DataFrame({"date": dates, "close": [100.0, 110.0, 121.0, 133.1]})
    benchmark = pl.DataFrame({"date": dates, "close": [100.0, 102.0, 104.0, 106.0]})

    momentum, momentum_col, _ = market_momentum_frame(left, 2, "factor_value")
    relative, relative_col, _ = market_relative_momentum_frame(left, benchmark, 2, "factor_value")

    assert momentum[momentum_col].to_list()[-2:] == pytest.approx([0.21, 0.21])
    assert relative[relative_col].to_list()[-1] == pytest.approx(0.1707843137)


def test_bond_momentum_is_forward_filled_on_a_share_calendar() -> None:
    source_dates = [date(2024, 1, 2), date(2024, 1, 3)]
    market_dates = [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    context = {
        "market": pl.DataFrame({"date": market_dates, "close": [1.0, 2.0, 3.0]}),
        "series": {
            "T0": pl.DataFrame(
                {"date": source_dates, "close": [100.0, 110.0]}
            )
        },
    }

    out, column, _ = EXTENDED_EVALUATORS["bond_futures_momentum"](context, 1)

    assert out["date"].to_list() == market_dates
    assert out[column].to_list() == pytest.approx([0.1, 0.1, 0.1])


def test_market_change_and_zscore_use_parameter_window() -> None:
    series = pl.DataFrame(
        {
            "date": _dates(5),
            "value": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )

    changed, changed_col, _ = market_change_frame(series, 2, "value", "factor_value")
    zscore, zscore_col, _ = rolling_zscore_frame(series, 3, "value", "factor_value")

    assert changed[changed_col].to_list()[-1] == pytest.approx(5.0 / 3.0 - 1.0)
    assert zscore[zscore_col].to_list()[-1] == pytest.approx(1.0)


def test_etf_share_change_is_broadcast_to_daily_market_calendar() -> None:
    market = pl.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 2, 2)],
        "close": [100.0, 101.0, 102.0],
    })
    context = {
        "market": market,
        "series": {
            "etf_510300_shares": pl.DataFrame({
                "date": [date(2024, 1, 1), date(2024, 2, 1)],
                "value": [100.0, 110.0],
            }),
        },
    }

    out, output_col, _ = etf_share_change(context, 1)

    assert out["date"].to_list() == [date(2024, 2, 2)]
    assert out[output_col].to_list() == pytest.approx([0.1])


def test_market_skew_is_negative_realized_skew() -> None:
    dates = _dates(6)
    series = pl.DataFrame(
        {
            "date": dates,
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 110.0],
        }
    )

    out, output_col, formula = market_skew_frame(series, 4, "factor_value")

    assert out[output_col].drop_nulls().len() == 2
    assert out[output_col].drop_nulls().last() < 0
    assert formula == "negative_realized_skew(HS300 return, 4)"


def test_rank_score_series_applies_direction_sign() -> None:
    frame = pl.DataFrame(
        {
            "factor": [1.0, 2.0, 3.0, 4.0],
            "forward_return": [1.0, 2.0, 3.0, 4.0],
        }
    )

    positive = rank_score_series(frame, "factor", "forward_return", 1)
    negative = rank_score_series(frame, "factor", "forward_return", -1)

    assert positive["score"].mean() > 0
    assert negative["score"].to_list() == pytest.approx(
        [-value for value in positive["score"].to_list()]
    )


def test_extended_evaluator_registry_has_all_p3_names() -> None:
    assert set(EXTENDED_EVALUATORS) == {
        "industry_momentum",
        "industry_rps",
        "industry_breadth",
        "stock_rel_industry_strength",
        "insider_buy_event",
        "insider_net_buy_event",
        "insider_sell_event_low",
        "lhb_net_buy_event",
        "lhb_inst_net_buy_event",
        "lhb_event_count_low",
        "repurchase_event_decay",
        "block_trade_ratio_event",
        "block_premium_event",
        "analyst_rating_level",
        "analyst_rating_change",
        "bond_futures_momentum",
        "bond_futures_trend_momentum",
        "hsi_momentum",
        "hscei_momentum",
        "hsi_relative_hs300",
        "etf_share_change",
        "qvix_zscore",
        "market_skew",
    }


def test_p3_current_formula_templates_match_registered_families() -> None:
    dates = _dates(6)
    symbols = ["A1", "A2", "B1", "B2"]
    calendar = pl.DataFrame(
        {
            "symbol": [symbol for symbol in symbols for _ in dates],
            "date": dates * len(symbols),
        }
    )
    price_rows = []
    for index, symbol in enumerate(symbols):
        industry = "A" if symbol.startswith("A") else "B"
        for offset, day in enumerate(dates):
            price_rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "close": 10.0 + offset * (0.2 if industry == "A" else -0.1) + index * 0.01,
                    "industry_l1": industry,
                }
            )
    prices = pl.DataFrame(price_rows)
    day = dates[0]
    context = {
        "calendar": calendar,
        "prices": prices,
        "market": pl.DataFrame(
            {
                "date": dates,
                "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
            }
        ),
        "events": {
            "insider": pl.DataFrame(
                {
                    "symbol": ["A1"],
                    "date": [day],
                    "buy": [1.0],
                    "sell": [0.5],
                }
            ),
            "lhb": pl.DataFrame(
                {
                    "symbol": ["A1"],
                    "date": [day],
                    "net_buy_ratio": [0.1],
                    "inst_net_buy_ratio": [0.05],
                    "event_count": [1.0],
                }
            ),
            "repurchase": pl.DataFrame(
                {
                    "symbol": ["A1"],
                    "date": [day],
                    "event": [1.0],
                }
            ),
            "block": pl.DataFrame(
                {
                    "symbol": ["A1"],
                    "date": [day],
                    "block_ratio": [0.01],
                    "premium": [0.02],
                }
            ),
        },
        "analyst_reports": pl.DataFrame(
            {
                "symbol": ["A1"],
                "date": [day],
                "rating": [4.0],
                "rating_change": [1.0],
            }
        ),
        "series": {
            "T0": pl.DataFrame(
                {"date": dates, "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]}
            ),
            "TF0": pl.DataFrame(
                {"date": dates, "close": [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]}
            ),
            "HSI": pl.DataFrame(
                {"date": dates, "close": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]}
            ),
            "HSCEI": pl.DataFrame(
                {"date": dates, "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]}
            ),
            "etf_510300_shares": pl.DataFrame(
                {
                    "date": dates,
                    "value": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
                }
            ),
            "qvix_300etf": pl.DataFrame(
                {
                    "date": dates,
                    "close": [10.0, 11.0, 12.0, 13.0, 12.0, 11.0],
                }
            ),
        },
    }
    root = Path(__file__).resolve().parents[3]
    registry = load_parameter_registry(root / "data")
    families = [family for family in registry.families if family.evaluator in EXTENDED_EVALUATORS]
    assert len(families) == 23
    for family in families:
        _, _, formula = EXTENDED_EVALUATORS[family.evaluator](context, family.current_parameter)
        assert formula == family.formula_template.format(parameter=family.current_parameter)
