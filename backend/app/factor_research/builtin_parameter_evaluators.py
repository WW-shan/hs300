"""Vectorized evaluators for built-in price/volume parameter families."""
from __future__ import annotations

from collections.abc import Callable, Collection

import polars as pl

Evaluator = Callable[[pl.DataFrame, int], tuple[pl.DataFrame, str, str]]
_FACTOR_COLUMN = "factor_value"
_ANNUALIZATION = 252**0.5


def _require(frame: pl.DataFrame, columns: Collection[str], parameter: int) -> None:
    if parameter <= 0:
        raise ValueError("parameter must be positive")
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"missing columns: {missing}")


def _result(frame: pl.DataFrame, expression: pl.Expr, formula: str) -> tuple[pl.DataFrame, str, str]:
    return (
        frame.select([
            "symbol",
            "date",
            expression.cast(pl.Float64).alias(_FACTOR_COLUMN),
        ]),
        _FACTOR_COLUMN,
        formula,
    )


def _ratio(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return pl.when(denominator.is_not_null() & (denominator != 0)).then(
        numerator / denominator
    ).otherwise(None)


def _relative(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    return _ratio(numerator, denominator) - 1.0


def _change() -> pl.Expr:
    return pl.col("close") / pl.col("close").shift(1).over("symbol") - 1.0


def _rolling(expression: pl.Expr, parameter: int) -> pl.Expr:
    return expression.rolling_mean(parameter, min_samples=parameter).over("symbol")


def _rolling_corr_expr(
    left: pl.Expr,
    right: pl.Expr,
    product: pl.Expr,
    window: int,
) -> pl.Expr:
    mean_left = left.rolling_mean(window, min_samples=window)
    mean_right = right.rolling_mean(window, min_samples=window)
    mean_product = product.rolling_mean(window, min_samples=window)
    mean_left_sq = (left * left).rolling_mean(window, min_samples=window)
    mean_right_sq = (right * right).rolling_mean(window, min_samples=window)
    covariance = mean_product - mean_left * mean_right
    variance_left = mean_left_sq - mean_left * mean_left
    variance_right = mean_right_sq - mean_right * mean_right
    return pl.when(
        (variance_left > 0) & (variance_right > 0)
    ).then(
        covariance / (variance_left * variance_right).sqrt()
    ).otherwise(None).over("symbol")


def momentum(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    expression = _relative(pl.col("close"), pl.col("close").shift(parameter).over("symbol"))
    return _result(frame, expression, f"close / close.shift({parameter}) - 1")


def ma_bias(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    mean = _rolling(pl.col("close"), parameter)
    return _result(frame, _relative(pl.col("close"), mean), f"close / rolling_mean(close, {parameter}) - 1")


def ema_bias(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    alpha = 2.0 / (parameter + 1.0)
    mean = pl.col("close").ewm_mean(alpha=alpha, adjust=False).over("symbol")
    return _result(frame, _relative(pl.col("close"), mean), f"close / EMA(close, alpha=2/({parameter}+1)) - 1")


def rsi(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    delta = pl.col("close").diff().over("symbol")
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    alpha = 1.0 / parameter
    avg_gain = gain.ewm_mean(alpha=alpha, adjust=False).over("symbol")
    avg_loss = loss.ewm_mean(alpha=alpha, adjust=False).over("symbol")
    expression = 100 - 100 / (
        1 + avg_gain / pl.when(avg_loss == 0).then(1e-12).otherwise(avg_loss)
    )
    return _result(
        frame,
        expression,
        f"RSI(close changes, Wilder alpha=1/{parameter})",
    )


def annual_volatility(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    daily = pl.col("close").pct_change().over("symbol")
    expression = daily.rolling_std(parameter, min_samples=parameter).over("symbol") * _ANNUALIZATION
    return _result(frame, expression, f"rolling_std(close.pct_change(), {parameter}) * sqrt(252)")


def downside_volatility(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    downside = _change().clip(upper_bound=0.0) ** 2
    expression = _rolling(downside, parameter).sqrt()
    return _result(frame, expression, f"sqrt(rolling_mean(min(change, 0)^2, {parameter}))")


def atr_pct(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "high", "low", "close"}, parameter)
    previous = pl.col("close").shift(1).over("symbol")
    true_range = pl.max_horizontal(
        pl.col("high") - pl.col("low"),
        (pl.col("high") - previous).abs(),
        (pl.col("low") - previous).abs(),
    )
    atr = true_range.ewm_mean(alpha=1.0 / parameter, adjust=False).over("symbol")
    return _result(frame, _ratio(atr, pl.col("close")), f"Wilder ATR({parameter}) / close")


def bollinger_position(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    mean = _rolling(pl.col("close"), parameter)
    std = pl.col("close").rolling_std(parameter, min_samples=parameter).over("symbol")
    upper = mean + 2 * std
    lower = mean - 2 * std
    return _result(frame, _ratio(pl.col("close") - lower, upper - lower), f"(close - lower Bollinger({parameter})) / band width")


def bollinger_width(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    mean = _rolling(pl.col("close"), parameter)
    std = pl.col("close").rolling_std(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _ratio(4 * std, mean), f"4 * rolling_std(close, {parameter}) / rolling_mean(close, {parameter})")


def price_position(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    high = pl.col("close").rolling_max(parameter, min_samples=parameter).over("symbol")
    low = pl.col("close").rolling_min(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _ratio(pl.col("close") - low, high - low), f"(close - rolling_min(close, {parameter})) / rolling range")


def distance_to_high(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    high = pl.col("close").rolling_max(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _relative(pl.col("close"), high), f"close / rolling_max(close, {parameter}) - 1")


def distance_from_low(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    low = pl.col("close").rolling_min(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _relative(pl.col("close"), low), f"close / rolling_min(close, {parameter}) - 1")


def amihud(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close", "amount"}, parameter)
    illiquidity = _ratio(_change().abs(), pl.col("amount") / 1e8)
    return _result(frame, _rolling(illiquidity, parameter), f"rolling_mean(abs(change) / (amount / 1e8), {parameter})")


def amount_level(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "amount"}, parameter)
    return _result(frame, _rolling(pl.col("amount") / 1e8, parameter), f"rolling_mean(amount / 1e8, {parameter})")


def turnover_level(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "turnover_rate"}, parameter)
    return _result(frame, _rolling(pl.col("turnover_rate"), parameter), f"rolling_mean(turnover_rate, {parameter})")


def turnover_stability(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "turnover_rate"}, parameter)
    mean = _rolling(pl.col("turnover_rate"), parameter)
    std = pl.col("turnover_rate").rolling_std(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _ratio(std, mean), f"rolling_std(turnover_rate, {parameter}) / rolling_mean(turnover_rate, {parameter})")


def turnover_crowding(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "turnover_rate"}, parameter)
    baseline = pl.col("turnover_rate").shift(1).over("symbol")
    mean = baseline.rolling_mean(parameter, min_samples=parameter).over("symbol")
    std = baseline.rolling_std(parameter, min_samples=parameter).over("symbol")
    expression = pl.when(std > 0).then(
        (pl.col("turnover_rate") - mean) / std
    ).otherwise(None)
    return _result(frame, expression, f"(turnover_rate - prior {parameter}d mean) / prior {parameter}d std")


def volume_ratio(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "volume"}, parameter)
    baseline = pl.col("volume").shift(1).rolling_mean(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _ratio(pl.col("volume"), baseline), f"volume / prior rolling_mean(volume, {parameter})")


def turnover_ratio(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "turnover_rate"}, parameter)
    baseline = pl.col("turnover_rate").shift(1).rolling_mean(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _relative(pl.col("turnover_rate"), baseline), f"turnover_rate / prior rolling_mean(turnover_rate, {parameter}) - 1")


def amount_ratio(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "amount"}, parameter)
    baseline = pl.col("amount").shift(1).rolling_mean(parameter, min_samples=parameter).over("symbol")
    return _result(frame, _relative(pl.col("amount"), baseline), f"amount / prior rolling_mean(amount, {parameter}) - 1")


def obv_trend(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close", "volume"}, parameter)
    change = _change()
    signed_volume = change.sign() * pl.col("volume")
    total = signed_volume.rolling_sum(parameter, min_samples=parameter).over("symbol")
    scale = pl.col("volume").rolling_mean(parameter, min_samples=parameter).over("symbol") * parameter
    return _result(frame, _ratio(total, scale), f"rolling_sum(sign(change) * volume, {parameter}) / (rolling_mean(volume, {parameter}) * {parameter})")


def volume_price_corr(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close", "volume"}, parameter)
    change = _change()
    volume = pl.col("volume")
    return _result(
        frame,
        _rolling_corr_expr(change, volume, change * volume, parameter),
        f"rolling_corr(change, volume, {parameter})",
    )


def limit_up_count(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "consecutive_limit_ups"}, parameter)
    hit = (pl.col("consecutive_limit_ups").fill_null(0) > 0).cast(pl.Float64)
    expression = hit.rolling_sum(parameter, min_samples=parameter).over("symbol")
    return _result(frame, expression, f"rolling_sum(limit_up_hit, {parameter})")


def max_return(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    expression = _change().rolling_max(parameter, min_samples=parameter).over("symbol")
    return _result(frame, expression, f"rolling_max(change, {parameter})")


def return_skew(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    expression = _change().rolling_skew(parameter, bias=True).over("symbol")
    return _result(frame, expression, f"rolling_skew(change, {parameter}, bias=True)")


def up_day_count(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "close"}, parameter)
    up = (_change() > 0).cast(pl.Float64)
    expression = up.rolling_sum(parameter, min_samples=parameter).over("symbol")
    return _result(frame, expression, f"rolling_sum(change > 0, {parameter})")


def overnight_return(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "open", "close"}, parameter)
    overnight = pl.col("open") / pl.col("close").shift(1).over("symbol") - 1.0
    expression = overnight.rolling_sum(parameter, min_samples=parameter).over("symbol")
    return _result(frame, expression, f"rolling_sum(open / prior close - 1, {parameter})")


def intraday_return(frame: pl.DataFrame, parameter: int) -> tuple[pl.DataFrame, str, str]:
    _require(frame, {"symbol", "date", "open", "close"}, parameter)
    intraday = pl.col("close") / pl.col("open") - 1.0
    expression = intraday.rolling_sum(parameter, min_samples=parameter).over("symbol")
    return _result(frame, expression, f"rolling_sum(close / open - 1, {parameter})")


BUILTIN_EVALUATORS: dict[str, Evaluator] = {
    "momentum": momentum,
    "ma_bias": ma_bias,
    "ema_bias": ema_bias,
    "rsi": rsi,
    "annual_volatility": annual_volatility,
    "downside_volatility": downside_volatility,
    "atr_pct": atr_pct,
    "bollinger_position": bollinger_position,
    "bollinger_width": bollinger_width,
    "price_position": price_position,
    "distance_to_high": distance_to_high,
    "distance_from_low": distance_from_low,
    "amihud": amihud,
    "amount_level": amount_level,
    "turnover_level": turnover_level,
    "turnover_stability": turnover_stability,
    "turnover_crowding": turnover_crowding,
    "volume_ratio": volume_ratio,
    "turnover_ratio": turnover_ratio,
    "amount_ratio": amount_ratio,
    "obv_trend": obv_trend,
    "volume_price_corr": volume_price_corr,
    "limit_up_count": limit_up_count,
    "max_return": max_return,
    "return_skew": return_skew,
    "up_day_count": up_day_count,
    "overnight_return": overnight_return,
    "intraday_return": intraday_return,
}
