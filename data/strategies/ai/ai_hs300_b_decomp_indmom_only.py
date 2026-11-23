"""R149 V2：仅行业动量。"""
import polars as pl

META = {
    "research_only": True,
    "id": "ai_hs300_b_decomp_indmom_only",
    "name": "HS300 B分解-仅行业动量",
    "description": "仅行业动量 100%（B 腿机制分解 V2，实验）",
    "tags": ["机制分解", "行业动量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8, "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60},
    "params": [],
    "scoring": {"ext_alt_hs300_industry_mom_20d": 1.0},
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_industry_mom_20d"}
RULES = """
1. 候选池沿用 B 腿：MA60 与行业动量因子必须有效。
2. 评分只使用行业动量，取当日截面高分标的。
3. 持有 20 个交易日或触发 -20% 止损后离场。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_industry_mom_20d").is_not_null()
