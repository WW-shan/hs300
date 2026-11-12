"""R362: test a five-session weak-regime intraday-reversal B sleeve."""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_intraday_reversal_weak_h5_sl20",
    "name": "R362 弱市行业强弱日内反转质量策略",
    "description": "R101 B 腿以低日内收益反转替换 20% 质量价值评分，弱市 T-1 入场，持有上限 5 日；研究用。",
    "tags": ["R362", "弱市", "日内反转", "行业相对强弱", "研究"],
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
        "uf_intraday_reversal": 0.20,
        "uf_quality_value_defensive": 0.10,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS: list[str] = []
EXIT_SIGNALS: list[str] = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 5
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "uf_intraday_reversal",
    "uf_quality_value_defensive",
    "uf_cash_quality",
}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
