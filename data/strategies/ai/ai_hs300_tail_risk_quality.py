"""HS300 低尾部风险复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_tail_risk_quality",
    "name": "HS300低尾部风险质量",
    "description": "低下行波动、低波动、正偏度、低彩票性、稳定波动状态复合 + 财务质量（实验）",
    "tags": ["尾部风险", "低波动", "偏度", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_tail_risk_low": 0.55,
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
1. 尾部风险复合对下行波动、年化波动、最大单日涨幅和波动状态使用负权重。
2. 评分 = 低尾部风险55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；该维度历史单因子不稳，组合结果以真实回测为准。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
