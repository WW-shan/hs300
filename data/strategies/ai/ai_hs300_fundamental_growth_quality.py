"""HS300 财报增长质量复合策略。"""
import polars as pl

META = {
    "id": "ai_hs300_fundamental_growth_quality",
    "name": "HS300财报增长质量",
    "description": "ROE/毛利率/净利率/营收与净利增速/低负债/现金含量/低应计复合（实验）",
    "tags": ["财报", "成长", "质量", "现金流", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_fundamental_growth_quality": 0.75,
        "uf_low_vol": 0.25,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {"ma60"}
RULES = """
1. 财报因子按公告日之后 PIT 生效。
2. 评分 = 财报增长质量75% + 低波动25%。
3. 35日持有、无固定止损；纯财报策略必须同时通过近一年门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
