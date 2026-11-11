"""Threshold indicators and constant event-holding evaluators."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import polars as pl

from app.factor_research.extended_parameter_evaluators import event_decay_factor

ThresholdKind = Literal["absolute", "quantile"]
ThresholdDirection = Literal["high", "low"]


def _require(frame: pl.DataFrame, columns: set[str]) -> None:
    missing = sorted(columns - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {missing}")


def threshold_indicator(
    frame: pl.DataFrame,
    parameter: float,
    mode: ThresholdKind,
    direction: ThresholdDirection,
    *,
    value_column: str = "factor_value",
    output_column: str = "factor_value",
) -> pl.DataFrame:
    """Convert a continuous factor into a daily pass/fail indicator."""
    _require(frame, {"symbol", "date", value_column})
    if mode == "quantile" and not 0.0 < parameter < 1.0:
        raise ValueError("quantile threshold must be inside (0, 1)")
    if mode not in {"absolute", "quantile"}:
        raise ValueError(f"unsupported threshold mode: {mode}")
    if direction not in {"high", "low"}:
        raise ValueError(f"unsupported threshold direction: {direction}")

    threshold = (
        pl.lit(float(parameter))
        if mode == "absolute"
        else pl.col(value_column)
        .quantile(float(parameter), interpolation="linear")
        .over("date")
    )
    passed = (
        pl.col(value_column) >= threshold
        if direction == "high"
        else pl.col(value_column) <= threshold
    )
    return frame.select(
        [
            "symbol",
            "date",
            pl.when(pl.col(value_column).is_null())
            .then(None)
            .when(passed)
            .then(1.0)
            .otherwise(0.0)
            .cast(pl.Float64)
            .alias(output_column),
        ]
    )


def event_holding_factor(
    events: pl.DataFrame,
    calendar: pl.DataFrame,
    value_column: str,
    holding_days: int,
    output_column: str,
    multiplier: float = 1.0,
    *,
    fill_zero: bool = True,
) -> pl.DataFrame:
    """Keep an event value constant for N trading days after its announcement."""
    if holding_days <= 0:
        raise ValueError("holding_days must be positive")
    return event_decay_factor(
        events,
        calendar,
        value_column,
        holding_days,
        output_column,
        multiplier,
        fill_zero=fill_zero,
        decay="step",
    )


def _event_evaluator(
    context: Mapping[str, Any],
    parameter: float,
    *,
    event_key: str,
    value_column: str,
    output_column: str,
    formula_event: str,
    multiplier: float = 1.0,
) -> tuple[pl.DataFrame, str, str]:
    holding_days = int(parameter)
    out = event_holding_factor(
        context["events"][event_key],
        context["calendar"],
        value_column,
        holding_days,
        output_column,
        multiplier,
        fill_zero=True,
    )
    return (
        out,
        output_column,
        f"constant_holding({formula_event}, {holding_days} trading days)",
    )


def insider_buy_holding(context: Mapping[str, Any], parameter: float):
    return _event_evaluator(
        context,
        parameter,
        event_key="insider",
        value_column="buy",
        output_column="factor_value",
        formula_event="insider_buy",
    )


def lhb_net_buy_holding(context: Mapping[str, Any], parameter: float):
    return _event_evaluator(
        context,
        parameter,
        event_key="lhb",
        value_column="net_buy_ratio",
        output_column="factor_value",
        formula_event="lhb_net_buy",
    )


def repurchase_holding(context: Mapping[str, Any], parameter: float):
    return _event_evaluator(
        context,
        parameter,
        event_key="repurchase",
        value_column="event",
        output_column="factor_value",
        formula_event="repurchase",
    )


def block_premium_holding(context: Mapping[str, Any], parameter: float):
    return _event_evaluator(
        context,
        parameter,
        event_key="block",
        value_column="premium",
        output_column="factor_value",
        formula_event="block_premium",
    )


def analyst_rating_change_holding(
    context: Mapping[str, Any],
    parameter: float,
):
    reports = context["analyst_reports"].select(
        [
            "symbol",
            "date",
            pl.col("rating_change").alias("rating_change"),
        ]
    )
    local = {**context, "events": {**context["events"], "analyst": reports}}
    return _event_evaluator(
        local,
        parameter,
        event_key="analyst",
        value_column="rating_change",
        output_column="factor_value",
        formula_event="analyst_rating_change",
    )


EVENT_HOLDING_EVALUATORS = {
    "insider_buy": insider_buy_holding,
    "lhb_net_buy": lhb_net_buy_holding,
    "repurchase": repurchase_holding,
    "block_premium": block_premium_holding,
    "analyst_rating_change": analyst_rating_change_holding,
}
