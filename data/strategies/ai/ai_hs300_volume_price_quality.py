"""HS300 量价结构复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_volume_price_quality",
    "name": "HS300量价结构质量",
    "description": "量价相关、OBV趋势、成交额放大、量能趋势、收盘位置复合 + 财务质量（实验）",
    "tags": ["量价", "交易结构", "情绪", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_volume_price_structure": 0.55,
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
1. 量价结构复合要求量价同向、OBV趋势改善、成交额/量能扩张和收盘位置偏强。
2. 评分 = 量价结构55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；交易结构因子只作为实验腿。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
