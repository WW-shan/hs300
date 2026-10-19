"""HS300 质量价值 + 现金质量匹配对照（R215）。"""
import polars as pl

META = {
    "id": "ai_hs300_market_skew_quality_control",
    "name": "HS300市场偏度匹配对照",
    "description": "与R215候选使用相同评分、股票池和执行口径，但不施加市场偏度正闸门",
    "tags": ["质量", "现金质量", "匹配对照", "沪深300", "实验", "R215"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "uf_quality_value_defensive": 0.545,
        "uf_cash_quality": 0.455,
    },
    "order_by": "score", "descending": True, "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_market_skew_60d_low",
}
RULES = """
1. 与R215候选使用相同评分和执行口径，但只要求市场偏度因子有效，不施加正偏度闸门。
2. 评分 = 质量价值防守 54.5% + 现金质量 45.5%。
3. 30只、20日持有、-20%固定止损；用于隔离市场偏度闸门的贡献。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_market_skew_60d_low").is_not_null()
    )