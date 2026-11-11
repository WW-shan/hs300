from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl
import pytest

from app.factor_research.builtin_parameter_evaluators import BUILTIN_EVALUATORS
from app.indicators.pipeline import compute_indicators
from app.strategy.scoring import scoring_value_expr


def _panel() -> pl.DataFrame:
    rows: list[dict] = []
    start = date(2023, 1, 1)
    for symbol_index, symbol in enumerate(("A.SH", "B.SH")):
        for index in range(320):
            close = 10.0 + symbol_index + index * 0.015 + math.sin(index / 7) * 0.35
            open_price = close * (1 + math.sin(index / 5) * 0.002)
            high = max(open_price, close) * 1.012
            low = min(open_price, close) * 0.988
            volume = 1_000_000 * (1.2 + math.sin(index / 11) * 0.25)
            rows.append({
                "symbol": symbol,
                "date": start + timedelta(days=index),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": close * volume,
                "turnover_rate": 1.5 + math.sin(index / 9) * 0.4,
                "consecutive_limit_ups": 1 if index % 37 == 0 else 0,
            })
    return compute_indicators(pl.DataFrame(rows).sort(["symbol", "date"]))


@pytest.mark.parametrize(
    ("evaluator", "parameter", "canonical_factor"),
    [
        ("momentum", 5, "momentum_5d"),
        ("momentum", 20, "momentum_20d"),
        ("ma_bias", 5, "ma5_bias"),
        ("ma_bias", 20, "ma20_bias"),
        ("ema_bias", 5, "ema5_bias"),
        ("ema_bias", 20, "ema20_bias"),
        ("rsi", 14, "rsi_14"),
        ("annual_volatility", 20, "annual_vol_20d"),
        ("downside_volatility", 20, "downside_vol_20d"),
        ("atr_pct", 14, "atr_pct"),
        ("bollinger_position", 20, "boll_position"),
        ("bollinger_width", 20, "boll_width"),
        ("price_position", 240, "position_240d"),
        ("distance_to_high", 240, "distance_to_high_240d"),
        ("distance_from_low", 60, "distance_from_low_60d"),
        ("amihud", 20, "amihud_20d"),
        ("amount_level", 20, "amount_mean_20d"),
        ("turnover_level", 20, "turnover_mean_20d"),
        ("turnover_stability", 20, "turnover_std_20d"),
        ("turnover_crowding", 60, "turnover_z_60d"),
        ("volume_ratio", 5, "vol_ratio_5d"),
        ("turnover_ratio", 5, "turnover_ratio_5d"),
        ("amount_ratio", 5, "amount_ratio_5d"),
        ("obv_trend", 20, "obv_trend_20d"),
        ("volume_price_corr", 20, "vol_price_corr_20d"),
        ("limit_up_count", 20, "limit_up_count_20d"),
        ("max_return", 20, "max_ret_20d"),
        ("return_skew", 20, "ret_skew_20d"),
        ("up_day_count", 20, "up_days_20d"),
        ("overnight_return", 20, "overnight_ret_20d"),
        ("intraday_return", 20, "intraday_ret_20d"),
    ],
)
def test_current_window_matches_canonical_factor_formula(
    evaluator: str,
    parameter: int,
    canonical_factor: str,
) -> None:
    panel = _panel()
    expression = scoring_value_expr(panel.columns, canonical_factor)
    assert expression is not None
    expected = panel.with_columns(expression.alias("expected"))
    actual, value_column, formula_text = BUILTIN_EVALUATORS[evaluator](panel, parameter)

    assert formula_text
    joined = expected.select(["symbol", "date", "expected"]).join(
        actual.select(["symbol", "date", pl.col(value_column).alias("actual")]),
        on=["symbol", "date"],
        how="inner",
    ).filter(pl.col("expected").is_not_null() & pl.col("actual").is_not_null())

    assert joined.height > 0
    difference = joined.select(
        (pl.col("expected") - pl.col("actual")).abs().max()
    ).item()
    assert float(difference) <= 1e-9


def test_evaluators_fail_closed_on_missing_required_columns() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        BUILTIN_EVALUATORS["momentum"](pl.DataFrame({"symbol": ["A"], "date": [date(2024, 1, 1)]}), 20)


def test_manifest_formula_templates_match_all_evaluators() -> None:
    import sys
    from pathlib import Path

    scripts = Path(__file__).resolve().parents[3] / "scripts"
    sys.path.insert(0, str(scripts))
    from research_builtin_parameter_families import load_p2_families

    panel = _panel()
    for family in load_p2_families(Path(__file__).resolve().parents[3] / "data"):
        _, _, formula = BUILTIN_EVALUATORS[family.evaluator](
            panel, family.current_parameter
        )
        assert family.formula_template is not None
        assert formula == family.formula_template.format(
            parameter=family.current_parameter
        ), family.family_id
