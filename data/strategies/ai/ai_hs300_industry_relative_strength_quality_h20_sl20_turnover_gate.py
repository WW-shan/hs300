"""HS300 industry-relative-strength quality with a turnover-regime gate (R204 experiment).

The stock-selection score, 10-position limit, 20-day holding period and -20%
stop-loss are identical to the R102d B sleeve. New entries are allowed only
when the 2017-extended HS300 turnover z-score is below zero.
"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_turnover_gate",
    "name": "HS300 industry relative strength quality (20d/stop20%/turnover gate)",
    "description": "R102d B sleeve plus hs300_turnover_gate_flag_ext2017; experimental R204 test",
    "tags": ["industry", "relative-strength", "quality", "turnover", "gate", "HS300", "experiment", "R204"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 10,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_hs300_turnover_gate_flag_ext2017",
}
RULES = """
1. Stock score and execution are identical to the R102d B sleeve.
2. New entries require hs300_turnover_gate_flag_ext2017 == 1 (turnover z-score < 0).
3. Hold 20 trading days; fixed -20% stop-loss; 10 positions.
4. R204 experiment; not a deployment recommendation.
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & (pl.col("ext_alt_hs300_hs300_turnover_gate_flag_ext2017") > 0)
    )
