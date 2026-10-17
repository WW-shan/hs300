"""HS300 流动性结构复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_liquidity_structure_quality",
    "name": "HS300流动性结构质量",
    "description": "低成交额/换手/换手波动、高非流动性、低成交额对数复合 + 财务质量（实验）",
    "tags": ["流动性", "Amihud", "换手", "成交额", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_liquidity_structure": 0.55,
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
1. 流动性结构复合偏好低成交额、低换手、低换手波动、高Amihud。
2. 评分 = 流动性结构55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；低流动性可能存在冲击成本，按项目成本口径验证。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
