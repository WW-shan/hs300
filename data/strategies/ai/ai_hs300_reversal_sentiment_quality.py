"""HS300 反转情绪复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_reversal_sentiment_quality",
    "name": "HS300反转情绪质量",
    "description": "短期反转、RSI回落、收盘位置、低换手/低彩票性复合 + 财务质量（实验）",
    "tags": ["反转", "情绪", "交易结构", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_reversal_sentiment": 0.55,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60"}
RULES = """
1. 反转情绪复合对短期动量、RSI变化、换手z和彩票性使用负权重。
2. 评分 = 反转情绪复合55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；R10/R13/R53 的单因子负知识不能替代本组合回测。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
