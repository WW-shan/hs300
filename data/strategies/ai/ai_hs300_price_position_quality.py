"""HS300 价格位置复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_price_position_quality",
    "name": "HS300价格位置质量",
    "description": "一年价格位置、距高点、距低点、布林位置、VWAP乖离复合 + 财务质量（实验）",
    "tags": ["价格位置", "趋势", "布林", "VWAP", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_price_position": 0.55,
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
1. 价格位置复合要求接近一年高点/60日低点上方，布林和VWAP位置偏强。
2. 评分 = 价格位置55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；位置族可能包含追高风险，以回测结果为准。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
