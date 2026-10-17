"""HS300 前十大流通股东集中度 + 质量价值。

实验型筹码集中度策略。R87 的 Discovery IC 为正、Validation 明显衰减，
因此本文件只提供可回测原型，不把历史强度当作稳定 alpha。
"""
import polars as pl

META = {
    "id": "ai_hs300_holder_quality",
    "name": "HS300筹码集中质量",
    "description": "前十大流通股东集中度 + 质量价值 + 现金质量（实验）",
    "tags": ["筹码", "股东集中度", "质量", "价值", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.2e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35

REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_holder_top10_free_float_share",
}

RULES = """
1. 候选池为当前沪深300成分，只使用公告日之后可见的前十大流通股东比例。
2. 评分 = 股东集中度 45% + 质量价值防守 30% + 现金质量 25%。
3. 持有满 35 个交易日强制换出；该类均值回复因子不加固定止损。
4. 实验策略；R87 的验证段 IC 衰减是已知风险。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    holder = pl.col("ext_alt_hs300_holder_top10_free_float_share")
    return pl.col("ma60").is_not_null() & holder.is_not_null()
