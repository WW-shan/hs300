"""R360 B-sleeve with a fixed industry-relative-strength fade exit."""
import polars as pl

META = {
    "id": "r371_industry_fade_5d_quality_h20_sl20",
    "name": "R360 HS300 行业相对强弱衰减质量策略",
    "description": "R101 B 袖原评分、20 日持有与 -20% 止损不变；行业相对强弱转负且5日继续走弱时退出。研究策略。",
    "tags": ["行业相对强弱", "质量", "策略结构", "研究"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.2e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS: list[str] = []
EXIT_SIGNALS = ["csg_r371_rel_strength_fade_5"]
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "uf_r371_fade_change_5d",
}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
