"""HS300 回购计划事件 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_repurchase_quality",
    "name": "HS300回购质量",
    "description": "回购计划公告 + 质量价值 + 现金质量 + 低波动（实验）",
    "tags": ["回购", "事件", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_repurchase_event_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_repurchase_event_60d",
}
RULES = """
1. 回购公告后下一交易日生效，60 个交易日内线性衰减。
2. 只保留近 60 日有回购公告的股票。
3. 评分 = 回购事件 40% + 质量 25% + 现金质量 20% + 低波动 15%。
4. 持有 20 个交易日换出；回购维度此前未过完整研究门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_repurchase_event_60d") > 0)
    )
