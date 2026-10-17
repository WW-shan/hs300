"""HS300 中期趋势复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_medium_term_trend_quality",
    "name": "HS300中期趋势质量",
    "description": "30日动量、MA/EMA多窗口乖离、MACD DIF复合 + 财务质量（实验）",
    "tags": ["趋势", "均线", "EMA", "MACD", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_medium_term_trend": 0.55,
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
1. 中期趋势复合要求30日动量、MA/EMA乖离和MACD DIF同向。
2. 评分 = 中期趋势55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；趋势族历史不稳，以真实回测为准。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
