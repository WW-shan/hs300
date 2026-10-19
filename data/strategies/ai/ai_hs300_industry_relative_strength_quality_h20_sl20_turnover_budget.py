"""HS300 B sleeve with full exposure under the turnover gate and top-3 partial exposure otherwise (R205)."""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_turnover_budget",
    "name": "HS300 industry relative strength quality (20d/stop20%/turnover budget)",
    "description": "R102d B sleeve; full entries when turnover gate is on, otherwise only top-3 HS300 relative-strength names",
    "tags": ["industry", "relative-strength", "quality", "turnover", "risk-budget", "HS300", "experiment", "R205"],
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
    "ext_alt_hs300_hs300_stock_rel_industry_top3_flag",
}
RULES = """
1. Stock score and execution are identical to the R102d B sleeve.
2. Gate on: full B-sleeve entries. Gate off: only top-3 HS300 relative-strength names may enter.
3. Hold 20 trading days; fixed -20% stop-loss; 10 positions maximum.
4. R205 partial-exposure experiment; not a deployment recommendation.
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    gate = pl.col("ext_alt_hs300_hs300_turnover_gate_flag_ext2017") > 0
    top3 = pl.col("ext_alt_hs300_hs300_stock_rel_industry_top3_flag") > 0
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & (gate | top3)
    )
