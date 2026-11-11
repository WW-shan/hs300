from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.factor_research.threshold_evaluators import (
    EVENT_HOLDING_EVALUATORS,
    event_holding_factor,
    threshold_indicator,
)


def _dates(count: int, start: date = date(2024, 1, 2)) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def test_absolute_threshold_indicator_selects_high_values() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "A", "A", "A"],
            "date": _dates(4),
            "factor_value": [1.0, 2.0, 3.0, 4.0],
        }
    )

    out = threshold_indicator(frame, 3.0, "absolute", "high")

    assert out["factor_value"].to_list() == [0.0, 0.0, 1.0, 1.0]


def test_quantile_threshold_indicator_uses_each_dates_cross_section() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"] * 2,
            "date": [date(2024, 1, 2)] * 4 + [date(2024, 1, 3)] * 4,
            "factor_value": [1.0, 2.0, 3.0, 4.0, 10.0, 20.0, 30.0, 40.0],
        }
    )

    out = threshold_indicator(frame, 0.75, "quantile", "high")

    assert out["factor_value"].to_list() == [0.0, 0.0, 0.0, 1.0] * 2


def test_low_direction_threshold_selects_lower_tail() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["A", "B", "C", "D"],
            "date": [date(2024, 1, 2)] * 4,
            "factor_value": [1.0, 2.0, 3.0, 4.0],
        }
    )

    out = threshold_indicator(frame, 0.25, "quantile", "low")

    assert out["factor_value"].to_list() == [1.0, 0.0, 0.0, 0.0]


def test_event_holding_is_constant_and_starts_next_trading_day() -> None:
    calendar = pl.DataFrame(
        {
            "symbol": ["A"] * 5,
            "date": _dates(5),
        }
    )
    events = pl.DataFrame(
        {
            "symbol": ["A"],
            "date": [date(2024, 1, 2)],
            "value": [2.0],
        }
    )

    out = event_holding_factor(
        events,
        calendar,
        "value",
        3,
        "factor_value",
    )

    assert out["factor_value"].to_list() == [0.0, 2.0, 2.0, 2.0, 0.0]


def test_event_holding_registry_has_all_five_families() -> None:
    assert set(EVENT_HOLDING_EVALUATORS) == {
        "insider_buy",
        "lhb_net_buy",
        "repurchase",
        "block_premium",
        "analyst_rating_change",
    }


def test_event_holding_evaluator_preserves_announcement_lag() -> None:
    dates = _dates(3)
    calendar = pl.DataFrame(
        {
            "symbol": ["A"] * 3,
            "date": dates,
        }
    )
    context = {
        "calendar": calendar,
        "events": {
            "lhb": pl.DataFrame(
                {
                    "symbol": ["A"],
                    "date": [dates[0]],
                    "net_buy_ratio": [0.5],
                }
            )
        },
    }

    out, column, formula = EVENT_HOLDING_EVALUATORS["lhb_net_buy"](context, 2)

    assert out[column].to_list() == [0.0, 0.5, 0.5]
    assert formula == "constant_holding(lhb_net_buy, 2 trading days)"


def test_analyst_holding_starts_next_trading_day_after_report() -> None:
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    calendar = pl.DataFrame({"symbol": ["A"] * len(dates), "date": dates})
    context = {
        "calendar": calendar,
        "events": {},
        "analyst_reports": pl.DataFrame(
            {
                "symbol": ["A"],
                "date": [dates[0]],
                "rating_change": [1.5],
            }
        ),
    }

    out, column, _ = EVENT_HOLDING_EVALUATORS["analyst_rating_change"](context, 2)

    assert out.sort("date")[column].to_list() == [0.0, 1.5, 1.5]


def test_event_holding_rejects_nonpositive_days() -> None:
    calendar = pl.DataFrame(
        {"symbol": ["A"], "date": [date(2024, 1, 2)]}
    )
    events = pl.DataFrame(
        {"symbol": ["A"], "date": [date(2024, 1, 2)], "value": [1.0]}
    )

    with pytest.raises(ValueError, match="holding_days"):
        event_holding_factor(events, calendar, "value", 0, "factor_value")
