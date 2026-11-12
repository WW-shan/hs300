"""R365: fixed two-part sentiment reversal sleeve for weak-regime 20-day holding."""
import polars as pl

META = {
    "id": "ai_hs300_weak_reversal_blend_industry_size_h20_sl20",
    "name": "R366 弱市情绪反转复合中性化 industry_size",
    "description": "评分和执行规则沿用 R365，仅对评分腿实施冻结的评分中性化研究。",
    "tags": ["R366", "弱市", "情绪", "反转", "行业相对强弱", "研究"],
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
        "cf_weak_reversal_blend": 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
    "research_only": True,
    "neutralize": {'industry': 'industry_l1', 'styles': ['log_float_mv'], 'exclude': [], 'shrink': 1.0},
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS: list[str] = []
EXIT_SIGNALS: list[str] = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "cf_weak_reversal_blend",
    "uf_quality_value_defensive",
    "uf_cash_quality",
}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
