"""HS300 行业内相对强弱 + 质量 + 现金质量复合策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_cash_quality",
    "name": "HS300行业强弱现金质量",
    "description": "行业内相对强弱、质量价值防守、现金质量复合，叠加行业RPS与低波动（实验）",
    "tags": ["行业中性", "相对强弱", "现金质量", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_industry_cash_quality": 0.65,
        "ext_alt_hs300_industry_rps_20d": 0.15,
        "uf_low_vol": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_rps_20d",
}
RULES = """
1. 复合因子对行业内相对强弱、质量价值防守、现金质量分别做当日截面 z-score 后加权。
2. 评分 = 复合因子 65% + 行业RPS 15% + 低波动 20%。
3. 只使用 T 日及以前数据，T+1 开盘成交；持有 35 个交易日换出。
4. R102 实验策略，不构成部署结论。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & pl.col("ext_alt_hs300_industry_rps_20d").is_not_null()
    )
