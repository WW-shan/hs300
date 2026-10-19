"""HS300 turnover-level timing with turnover history extended through 2017 (R203 experiment).

Monthly signal: hold an equal-weight HS300 basket when the HS300 member
turnover-rate 20d mean expanding z-score is below zero; otherwise stay in cash.
The signal formula is identical to R201; only the historical warmup is extended
through 2017 so the 2018-2026 coverage test is not penalized by a missing
12-month warmup window.
"""
import polars as pl

META = {
    "id": "ai_hs300_turnover_level_gate_ext2017",
    "name": "HS300 turnover-level timing (2017 history extended)",
    "description": "HS300 turnover 20d mean expanding z-score <0 (cool trading) holds HS300, otherwise cash (R203 experiment)",
    "tags": ["turnover", "crowding", "timing", "HS300", "experiment", "R203"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_hs300_turnover_z_ext2017": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_hs300_turnover_z_ext2017",
}
RULES = """
1. Signal = HS300 member turnover-rate 20d mean expanding z-score, extended with 2017 history.
2. Signal <0 (cool trading) holds an equal-weight HS300 basket; otherwise cash.
3. Signal is formed at month-end and executed at the next trading-day open; hold for 20 trading days.
4. R203 experiment; not a deployment recommendation.
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """Monthly signal: only enter on the last trading day of each month."""
    del df, params
    next_day = pl.col("date") + pl.duration(days=1)
    is_month_end = next_day.dt.month() != pl.col("date").dt.month()
    return (
        is_month_end
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_hs300_turnover_z_ext2017").is_not_null()
        & (pl.col("ext_alt_hs300_hs300_turnover_z_ext2017") < 0)
    )
