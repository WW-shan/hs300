"""HS300 反转情绪 + 低尾部风险 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_sentiment_tail_combo",
    "name": "HS300情绪尾部组合",
    "description": "反转情绪与低尾部风险两条复合腿 + 质量价值（实验）",
    "tags": ["反转", "情绪", "尾部风险", "低波动", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_reversal_sentiment": 0.35,
        "cf_tail_risk_low": 0.35,
        "uf_quality_value_defensive": 0.30,
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
1. 评分 = 反转情绪35% + 低尾部风险35% + 质量价值30%。
2. 20日持有、-20%止损。
3. 组合更多不自动更好；只有通过历史名单、2×/3×成本和近一年门槛才进入下一轮。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
