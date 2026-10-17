"""HS300 趋势确认复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_trend_confirmation_quality",
    "name": "HS300趋势确认质量",
    "description": "均线乖离、MACD、KDJ、距高点、量能趋势复合 + 财务质量（实验）",
    "tags": ["趋势", "MACD", "KDJ", "量价", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_trend_confirmation": 0.55,
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
1. 趋势确认复合要求均线乖离、MACD/KDJ、距高点和量能趋势同向。
2. 评分 = 趋势确认55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；趋势族此前多为负知识，本策略只验证复合形式。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
