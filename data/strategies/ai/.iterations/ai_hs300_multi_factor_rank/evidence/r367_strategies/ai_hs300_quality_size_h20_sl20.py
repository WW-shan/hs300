"""R367 matched control: original B scores with a 20-session holding cap."""
import polars as pl

META = {
    "id": "ai_hs300_quality_size_h20_sl20",
    "name": "R367 仅规模中性质量对照",
    "description": "仅对全部评分腿按流通市值代理做横截面残差化；其余规则沿用 R367。",
    "tags": ["R367", "弱市", "行业相对强弱", "匹配对照", "研究"],
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
    "research_only": True,
    "neutralize": {"styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
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
    "uf_quality_value_defensive",
    "uf_cash_quality",
}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
