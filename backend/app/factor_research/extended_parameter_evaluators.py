"""Parameterized evaluators for P3 event, industry, and market factors."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from typing import Any

import polars as pl

ExtendedEvaluator = Callable[
    [Mapping[str, Any], int],
    tuple[pl.DataFrame, str, str],
]

_FACTOR_COLUMN = "factor_value"


def _require(frame: pl.DataFrame, columns: Collection[str], parameter: int) -> None:
    if parameter <= 0:
        raise ValueError("parameter must be positive")
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {missing}")


def _result(
    frame: pl.DataFrame,
    expression: pl.Expr,
    output_column: str,
    formula: str,
    *,
    extra_columns: Collection[str] = (),
) -> tuple[pl.DataFrame, str, str]:
    return (
        frame.select(
            [
                "symbol",
                "date",
                *extra_columns,
                expression.cast(pl.Float64).alias(output_column),
            ]
        ),
        output_column,
        formula,
    )


def event_decay_factor(
    events: pl.DataFrame,
    calendar: pl.DataFrame,
    value_column: str,
    window: int,
    output_column: str,
    multiplier: float = 1.0,
    *,
    fill_zero: bool = False,
) -> pl.DataFrame:
    """Linear-decay daily events over the supplied symbol trading calendar.

    An event dated D first affects the first calendar date strictly after D and
    remains visible with weight ``1 - offset / window`` for ``window`` trading
    days. Same-symbol/same-day events are summed before the decay is applied.
    """
    _require(calendar, {"symbol", "date"}, window)
    _require(events, {"symbol", "date", value_column}, window)
    if window <= 0:
        raise ValueError("window must be positive")

    calendar = calendar.select(["symbol", "date"]).sort(["symbol", "date"])
    duplicate_keys = calendar.group_by(["symbol", "date"]).len().filter(pl.col("len") > 1)
    if not duplicate_keys.is_empty():
        raise ValueError("calendar contains duplicate symbol/date keys")
    indexed = calendar.with_columns(
        pl.int_range(pl.len()).over("symbol").cast(pl.Int64).alias("_trade_index")
    )
    if events.is_empty():
        if fill_zero:
            return calendar.select(
                [
                    "symbol",
                    "date",
                    pl.lit(0.0).cast(pl.Float64).alias(output_column),
                ]
            )
        return pl.DataFrame(
            schema={
                "symbol": pl.String,
                "date": pl.Date,
                output_column: pl.Float64,
            }
        )

    grouped_events = (
        events.select(
            [
                pl.col("symbol").cast(pl.String),
                pl.col("date").cast(pl.Date),
                pl.col(value_column).cast(pl.Float64),
            ]
        )
        .drop_nulls(["symbol", "date", value_column])
        .group_by(["symbol", "date"])
        .agg(pl.col(value_column).sum().alias("_event_value"))
        .with_columns((pl.col("date") + pl.duration(days=1)).alias("_effective_after"))
        .sort(["symbol", "_effective_after"])
    )
    mapped = grouped_events.join_asof(
        indexed.select(
            [
                "symbol",
                pl.col("date").alias("_calendar_date"),
                "_trade_index",
            ]
        ).sort(["symbol", "_calendar_date"]),
        left_on="_effective_after",
        right_on="_calendar_date",
        by="symbol",
        strategy="forward",
        check_sortedness=False,
    ).filter(pl.col("_trade_index").is_not_null())
    if mapped.is_empty():
        if fill_zero:
            return calendar.select(
                [
                    "symbol",
                    "date",
                    pl.lit(0.0).cast(pl.Float64).alias(output_column),
                ]
            )
        return pl.DataFrame(
            schema={
                "symbol": pl.String,
                "date": pl.Date,
                output_column: pl.Float64,
            }
        )

    offsets = pl.DataFrame(
        {
            "offset": list(range(window)),
            "weight": [1.0 - index / window for index in range(window)],
        }
    )
    expanded = (
        mapped.join(offsets, how="cross")
        .with_columns(
            (pl.col("_trade_index") + pl.col("offset")).alias("_target_index"),
            (pl.col("_event_value") * pl.col("weight") * float(multiplier)).alias(
                "_weighted_value"
            ),
        )
        .join(
            indexed.select(
                [
                    "symbol",
                    pl.col("_trade_index").alias("_max_index"),
                ]
            )
            .group_by("symbol")
            .agg(pl.col("_max_index").max()),
            on="symbol",
            how="left",
        )
        .filter(pl.col("_target_index") <= pl.col("_max_index"))
    )
    scores = expanded.group_by(["symbol", "_target_index"]).agg(
        pl.col("_weighted_value").sum().alias(output_column)
    )
    joined = indexed.join(
        scores,
        left_on=["symbol", "_trade_index"],
        right_on=["symbol", "_target_index"],
        how="left" if fill_zero else "inner",
    )
    if fill_zero:
        return (
            joined.with_columns(pl.col(output_column).fill_null(0.0))
            .select(["symbol", "date", output_column])
            .sort(["symbol", "date"])
        )
    return (
        joined.filter(
            pl.col(output_column).is_not_null() & (pl.col(output_column) != 0.0)
        )
        .select(["symbol", "date", output_column])
        .sort(["symbol", "date"])
    )


def industry_window_frame(
    prices: pl.DataFrame,
    kind: str,
    window: int,
) -> tuple[pl.DataFrame, str, str]:
    """Build price-only industry momentum, RPS, breadth, or relative strength."""
    _require(prices, {"symbol", "date", "close", "industry_l1"}, window)
    if kind not in {"momentum", "rps", "breadth", "stock_relative"}:
        raise ValueError(f"unsupported industry window kind: {kind}")
    panel = (
        prices.select(["symbol", "date", "close", "industry_l1"])
        .filter(pl.col("industry_l1").is_not_null())
        .sort(["symbol", "date"])
        .with_columns(
            [
                (pl.col("close") / pl.col("close").shift(window).over("symbol") - 1.0).alias(
                    "_stock_return"
                ),
                (
                    pl.col("close")
                    > pl.col("close").rolling_mean(window, min_samples=window).over("symbol")
                )
                .cast(pl.Float64)
                .alias("_above_ma"),
            ]
        )
    )
    grouped = (
        panel.group_by(["date", "industry_l1"])
        .agg(
            [
                pl.col("_stock_return").mean().alias("_industry_return"),
                pl.col("_above_ma").mean().alias("_industry_breadth"),
                pl.len().alias("_industry_count"),
            ]
        )
        .with_columns(
            (pl.col("_industry_return").rank("ordinal").over("date") / pl.len().over("date")).alias(
                "_industry_rps"
            )
        )
    )
    joined = panel.join(grouped, on=["date", "industry_l1"], how="left")
    if kind == "momentum":
        return _result(
            joined,
            pl.col("_industry_return"),
            _FACTOR_COLUMN,
            f"industry equal-weight close / close.shift({window}) - 1",
            extra_columns=["industry_l1"],
        )
    if kind == "rps":
        return _result(
            joined,
            pl.col("_industry_rps"),
            _FACTOR_COLUMN,
            f"cross-industry ordinal rank of industry momentum({window}) / industry_count",
            extra_columns=["industry_l1"],
        )
    if kind == "breadth":
        return _result(
            joined,
            pl.col("_industry_breadth"),
            _FACTOR_COLUMN,
            f"mean(close > rolling_mean(close, {window})) by industry",
            extra_columns=["industry_l1"],
        )
    return _result(
        joined,
        pl.col("_stock_return") - pl.col("_industry_return"),
        _FACTOR_COLUMN,
        f"stock_return({window}) - industry equal-weight return({window})",
        extra_columns=["industry_l1"],
    )


def market_momentum_frame(
    series: pl.DataFrame,
    window: int,
    output_column: str = _FACTOR_COLUMN,
    *,
    source_label: str = "close",
) -> tuple[pl.DataFrame, str, str]:
    _require(series, {"date", "close"}, window)
    clean = series.select(["date", "close"]).unique(subset=["date"], keep="last").sort("date")
    out = clean.select(
        [
            "date",
            (pl.col("close") / pl.col("close").shift(window) - 1.0)
            .cast(pl.Float64)
            .alias(output_column),
        ]
    )
    return out, output_column, f"{source_label} close / close.shift({window}) - 1"


def _asof_market_signal(
    signal: pl.DataFrame,
    market: pl.DataFrame,
    value_column: str,
    *,
    effective_days: int = 0,
) -> pl.DataFrame:
    _require(signal, {"date", value_column}, 1)
    _require(market, {"date"}, 1)
    effective = (
        signal.filter(pl.col(value_column).is_not_null())
        .select(
            [
                (
                    pl.col("date") + pl.duration(days=effective_days)
                ).alias("_effective"),
                value_column,
            ]
        )
        .unique(subset=["_effective"], keep="last")
        .sort("_effective")
    )
    return (
        market.select("date")
        .unique()
        .sort("date")
        .join_asof(
            effective,
            left_on="date",
            right_on="_effective",
            strategy="backward",
            check_sortedness=False,
        )
        .filter(pl.col(value_column).is_not_null())
        .select(["date", value_column])
        .sort("date")
    )


def market_relative_momentum_frame(
    left: pl.DataFrame,
    benchmark: pl.DataFrame,
    window: int,
    output_column: str = _FACTOR_COLUMN,
) -> tuple[pl.DataFrame, str, str]:
    _require(left, {"date", "close"}, window)
    _require(benchmark, {"date", "close"}, window)
    left_ret = left.select(
        [
            "date",
            (pl.col("close") / pl.col("close").shift(window) - 1.0).alias("_left"),
        ]
    ).unique(subset=["date"], keep="last")
    benchmark_ret = benchmark.select(
        [
            "date",
            (pl.col("close") / pl.col("close").shift(window) - 1.0).alias("_benchmark"),
        ]
    ).unique(subset=["date"], keep="last")
    out = (
        left_ret.join(benchmark_ret, on="date", how="inner")
        .select(
            [
                "date",
                (pl.col("_left") - pl.col("_benchmark")).cast(pl.Float64).alias(output_column),
            ]
        )
        .sort("date")
    )
    return (
        out,
        output_column,
        f"HSI_return({window}) - HS300_return({window})",
    )


def market_change_frame(
    series: pl.DataFrame,
    window: int,
    value_column: str,
    output_column: str = _FACTOR_COLUMN,
    multiplier: float = 1.0,
    *,
    source_label: str | None = None,
    formula_template: str | None = None,
) -> tuple[pl.DataFrame, str, str]:
    _require(series, {"date", value_column}, window)
    clean = series.select(["date", value_column]).unique(subset=["date"], keep="last").sort("date")
    out = clean.select(
        [
            "date",
            (pl.col(value_column) / pl.col(value_column).shift(window) - 1.0)
            .cast(pl.Float64)
            .mul(float(multiplier))
            .alias(output_column),
        ]
    )
    label = source_label or value_column
    template = formula_template or "{label} / {label}.shift({parameter}) - 1"
    return out, output_column, template.format(label=label, parameter=window)


def rolling_zscore_frame(
    series: pl.DataFrame,
    window: int,
    value_column: str,
    output_column: str = _FACTOR_COLUMN,
    multiplier: float = 1.0,
    *,
    source_label: str | None = None,
) -> tuple[pl.DataFrame, str, str]:
    _require(series, {"date", value_column}, window)
    clean = series.select(["date", value_column]).unique(subset=["date"], keep="last").sort("date")
    mean = pl.col(value_column).rolling_mean(window, min_samples=window)
    std = pl.col(value_column).rolling_std(window, min_samples=window)
    out = clean.select(
        [
            "date",
            pl.when(std > 0.0)
            .then((pl.col(value_column) - mean) / std * float(multiplier))
            .otherwise(None)
            .cast(pl.Float64)
            .alias(output_column),
        ]
    )
    label = source_label or value_column
    return out, output_column, f"zscore({label}, {window})"


def market_skew_frame(
    series: pl.DataFrame,
    window: int,
    output_column: str = _FACTOR_COLUMN,
) -> tuple[pl.DataFrame, str, str]:
    _require(series, {"date", "close"}, window)
    out = (
        series.select(["date", "close"])
        .unique(subset=["date"], keep="last")
        .sort("date")
        .with_columns((pl.col("close") / pl.col("close").shift(1) - 1.0).alias("_ret"))
        .with_columns(
            [
                pl.col("_ret").rolling_sum(window, min_samples=window).alias("_s1"),
                pl.col("_ret").pow(2).rolling_sum(window, min_samples=window).alias("_s2"),
                pl.col("_ret").pow(3).rolling_sum(window, min_samples=window).alias("_s3"),
            ]
        )
        .with_columns(
            [
                (pl.col("_s1") / window).alias("_mean"),
                (pl.col("_s2") / window - (pl.col("_s1") / window).pow(2)).alias("_m2"),
                (
                    pl.col("_s3") / window
                    - 3.0 * (pl.col("_s1") / window) * (pl.col("_s2") / window)
                    + 2.0 * (pl.col("_s1") / window).pow(3)
                ).alias("_m3"),
            ]
        )
        .select(
            [
                "date",
                pl.when(pl.col("_m2") > 1e-12)
                .then(-pl.col("_m3") / pl.col("_m2").pow(1.5))
                .otherwise(None)
                .cast(pl.Float64)
                .alias(output_column),
            ]
        )
    )
    return out, output_column, f"negative_realized_skew(HS300 return, {window})"


def rank_score_series(
    frame: pl.DataFrame,
    factor_column: str,
    return_column: str,
    sign: int,
) -> pl.DataFrame:
    _require(frame, {factor_column, return_column}, abs(sign) or 1)
    clean = frame.filter(
        pl.col(factor_column).is_not_null()
        & pl.col(factor_column).is_finite()
        & pl.col(return_column).is_not_null()
        & pl.col(return_column).is_finite()
    )
    ranked = clean.with_columns(
        [
            pl.col(factor_column).rank("average").alias("_factor_rank"),
            pl.col(return_column).rank("average").alias("_return_rank"),
        ]
    )
    for column, output in (("_factor_rank", "_factor_z"), ("_return_rank", "_return_z")):
        mean = pl.col(column).mean()
        std = pl.col(column).std(ddof=0)
        ranked = ranked.with_columns(
            pl.when(std > 0.0).then((pl.col(column) - mean) / std).otherwise(0.0).alias(output)
        )
    return ranked.select([(pl.col("_factor_z") * pl.col("_return_z") * float(sign)).alias("score")])


def _event_evaluator(
    context: Mapping[str, Any],
    parameter: int,
    *,
    event_key: str,
    value_column: str,
    output_column: str,
    multiplier: float,
    formula: str,
    fill_zero: bool = False,
) -> tuple[pl.DataFrame, str, str]:
    events = context["events"][event_key]
    calendar = context["calendar"]
    out = event_decay_factor(
        events,
        calendar,
        value_column,
        parameter,
        output_column,
        multiplier,
        fill_zero=fill_zero,
    )
    return out, output_column, formula.format(parameter=parameter)


def insider_buy_event(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="insider",
        value_column="buy",
        output_column="insider_buy",
        multiplier=1.0,
        formula="linear_decay(insider_buy_events, {parameter} trading days)",
        fill_zero=True,
    )


def insider_net_buy_event(context: Mapping[str, Any], parameter: int):
    events = context["events"]["insider"].with_columns(
        (pl.col("buy").fill_null(0.0) - pl.col("sell").fill_null(0.0)).alias("net_buy")
    )
    local = {**context, "events": {**context["events"], "insider_net": events}}
    return _event_evaluator(
        local,
        parameter,
        event_key="insider_net",
        value_column="net_buy",
        output_column="insider_net_buy",
        multiplier=1.0,
        formula="linear_decay(insider_net_buy_events, {parameter} trading days)",
        fill_zero=True,
    )


def insider_sell_event_low(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="insider",
        value_column="sell",
        output_column="insider_sell_low",
        multiplier=-1.0,
        formula="-linear_decay(insider_sell_events, {parameter} trading days)",
        fill_zero=True,
    )


def lhb_net_buy_event(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="lhb",
        value_column="net_buy_ratio",
        output_column="lhb_net_buy",
        multiplier=1.0,
        formula="linear_decay(lhb_net_buy_ratio, {parameter} trading days)",
        fill_zero=True,
    )


def lhb_inst_net_buy_event(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="lhb",
        value_column="inst_net_buy_ratio",
        output_column="lhb_inst_net_buy",
        multiplier=1.0,
        formula="linear_decay(lhb_inst_net_buy_ratio, {parameter} trading days)",
        fill_zero=True,
    )


def lhb_event_count_low(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="lhb",
        value_column="event_count",
        output_column="lhb_event_count_low",
        multiplier=-1.0,
        formula="-linear_decay(lhb_event_count, {parameter} trading days)",
        fill_zero=True,
    )


def repurchase_event_decay(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="repurchase",
        value_column="event",
        output_column="repurchase_event",
        multiplier=1.0,
        formula="linear_decay(repurchase_event, {parameter} trading days)",
        fill_zero=True,
    )


def block_trade_ratio_event(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="block",
        value_column="block_ratio",
        output_column="block_trade_ratio_low",
        multiplier=-1.0,
        formula="-linear_decay(block_trade_ratio, {parameter} trading days)",
        fill_zero=True,
    )


def block_premium_event(context: Mapping[str, Any], parameter: int):
    return _event_evaluator(
        context,
        parameter,
        event_key="block",
        value_column="premium",
        output_column="block_premium",
        multiplier=1.0,
        formula="linear_decay(block_premium, {parameter} trading days)",
        fill_zero=True,
    )


def analyst_rating_level(context: Mapping[str, Any], parameter: int):
    reports = context["analyst_reports"]
    calendar = context["calendar"]
    _require(reports, {"symbol", "date", "rating"}, parameter)
    daily = (
        reports.select(
            [
                "symbol",
                pl.col("date").alias("_report_date"),
                "rating",
            ]
        )
        .filter(pl.col("symbol").is_not_null() & pl.col("_report_date").is_not_null())
        .group_by(["symbol", "_report_date"])
        .agg(pl.col("rating").mean().alias("_rating"))
        .with_columns(
            (pl.col("_report_date") + pl.duration(days=1)).alias("_effective")
        )
        .sort(["symbol", "_effective"])
    )
    joined = (
        calendar.select(["symbol", "date"])
        .sort(["symbol", "date"])
        .join_asof(
            daily,
            left_on="date",
            right_on="_effective",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .with_columns(
            (pl.col("date") - pl.col("_report_date"))
            .dt.total_days()
            .alias("_rating_age")
        )
        .with_columns(
            pl.when(pl.col("_rating_age").is_between(0, 180, closed="both"))
            .then(pl.col("_rating"))
            .otherwise(None)
            .alias("_valid_rating")
        )
        .with_columns(
            pl.col("_valid_rating")
            .rolling_mean(parameter, min_samples=1)
            .over("symbol")
            .alias("analyst_rating_level")
        )
        .filter(pl.col("analyst_rating_level").is_not_null())
        .select(["symbol", "date", "analyst_rating_level"])
    )
    return (
        joined,
        "analyst_rating_level",
        f"rolling_mean(asof analyst rating, {parameter})",
    )


def analyst_rating_change(context: Mapping[str, Any], parameter: int):
    reports = context["analyst_reports"]
    local_reports = reports.select(
        [
            "symbol",
            (pl.col("date") + pl.duration(days=1)).alias("date"),
            pl.col("rating_change").alias("_rating_change"),
        ]
    )
    return _event_evaluator(
        {**context, "events": {**context["events"], "analyst": local_reports}},
        parameter,
        event_key="analyst",
        value_column="_rating_change",
        output_column="analyst_rating_change",
        multiplier=1.0,
        formula="linear_decay(analyst_rating_change, {parameter} trading days)",
        fill_zero=True,
    )


def _industry_evaluator(kind: str):
    def evaluate(context: Mapping[str, Any], parameter: int):
        return industry_window_frame(context["prices"], kind, parameter)

    return evaluate


def _market_momentum_evaluator(series_key: str, source_label: str):
    def evaluate(context: Mapping[str, Any], parameter: int):
        signal, output_column, formula = market_momentum_frame(
            context["series"][series_key],
            parameter,
            source_label=source_label,
        )
        return (
            _asof_market_signal(signal, context["market"], output_column),
            output_column,
            formula,
        )

    return evaluate


def hsi_relative_hs300(context: Mapping[str, Any], parameter: int):
    left, left_column, _ = market_momentum_frame(
        context["series"]["HSI"],
        parameter,
        source_label="HSI",
    )
    benchmark, benchmark_column, _ = market_momentum_frame(
        context["market"],
        parameter,
        source_label="HS300",
    )
    aligned_left = _asof_market_signal(left, context["market"], left_column)
    out = (
        aligned_left.join(
            benchmark.select(
                ["date", pl.col(benchmark_column).alias("_benchmark")]
            ),
            on="date",
            how="inner",
        )
        .select(
            [
                "date",
                (pl.col(left_column) - pl.col("_benchmark"))
                .cast(pl.Float64)
                .alias(_FACTOR_COLUMN),
            ]
        )
        .sort("date")
    )
    return (
        out,
        _FACTOR_COLUMN,
        f"HSI_return({parameter}) - HS300_return({parameter})",
    )


def etf_share_change(context: Mapping[str, Any], parameter: int):
    monthly, output_column, formula = market_change_frame(
        context["series"]["etf_510300_shares"],
        parameter,
        "value",
        source_label="510300 shares",
        formula_template="510300 shares / shares.shift({parameter} months) - 1",
    )
    signal = (
        monthly.filter(pl.col(output_column).is_not_null())
        .with_columns((pl.col("date") + pl.duration(days=1)).alias("_effective"))
        .select(["_effective", output_column])
        .sort("_effective")
    )
    calendar = context["market"].select("date").unique().sort("date")
    out = (
        calendar.join_asof(
            signal,
            left_on="date",
            right_on="_effective",
            strategy="backward",
            check_sortedness=False,
        )
        .with_columns((pl.col("date") - pl.col("_effective")).dt.total_days().alias("_age"))
        .filter(
            pl.col("_age").is_between(0, 45, closed="both")
            & pl.col(output_column).is_not_null()
        )
        .select(["date", output_column])
        .sort("date")
    )
    return out, output_column, formula


def qvix_zscore(context: Mapping[str, Any], parameter: int):
    signal, output_column, formula = rolling_zscore_frame(
        context["series"]["qvix_300etf"],
        parameter,
        "close",
        source_label="QVIX close",
    )
    return (
        _asof_market_signal(signal, context["market"], output_column),
        output_column,
        formula,
    )


def market_skew(context: Mapping[str, Any], parameter: int):
    return market_skew_frame(context["market"], parameter)


EXTENDED_EVALUATORS: dict[str, ExtendedEvaluator] = {
    "industry_momentum": _industry_evaluator("momentum"),
    "industry_rps": _industry_evaluator("rps"),
    "industry_breadth": _industry_evaluator("breadth"),
    "stock_rel_industry_strength": _industry_evaluator("stock_relative"),
    "insider_buy_event": insider_buy_event,
    "insider_net_buy_event": insider_net_buy_event,
    "insider_sell_event_low": insider_sell_event_low,
    "lhb_net_buy_event": lhb_net_buy_event,
    "lhb_inst_net_buy_event": lhb_inst_net_buy_event,
    "lhb_event_count_low": lhb_event_count_low,
    "repurchase_event_decay": repurchase_event_decay,
    "block_trade_ratio_event": block_trade_ratio_event,
    "block_premium_event": block_premium_event,
    "analyst_rating_level": analyst_rating_level,
    "analyst_rating_change": analyst_rating_change,
    "bond_futures_momentum": _market_momentum_evaluator("T0", "T0"),
    "bond_futures_trend_momentum": _market_momentum_evaluator("TF0", "TF0"),
    "hsi_momentum": _market_momentum_evaluator("HSI", "HSI"),
    "hscei_momentum": _market_momentum_evaluator("HSCEI", "HSCEI"),
    "hsi_relative_hs300": hsi_relative_hs300,
    "etf_share_change": etf_share_change,
    "qvix_zscore": qvix_zscore,
    "market_skew": market_skew,
}
