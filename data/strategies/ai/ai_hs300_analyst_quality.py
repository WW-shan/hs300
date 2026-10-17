"""HS300 分析师评级 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_analyst_quality",
    "name": "HS300分析师评级质量",
    "description": "分析师评级水平/调整 + 质量价值 + 现金质量（实验）",
    "tags": ["分析师", "评级", "预期", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_analyst_rating_score_20d": 0.35,
        "ext_alt_hs300_analyst_rating_change_20d": 0.25,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_analyst_rating_score_20d",
    "ext_alt_hs300_analyst_rating_change_20d",
}
RULES = """
1. 分析师报告后下一交易日生效；评级水平最多保留 180 天。
2. 评分 = 评级水平 35% + 评级调整 25% + 质量 25% + 现金质量 15%。
3. 无分析师覆盖不补中性值，不参与排序。
4. 持有 35 个交易日换出；评级维度此前未过完整研究门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_analyst_rating_score_20d").is_not_null()
    )
