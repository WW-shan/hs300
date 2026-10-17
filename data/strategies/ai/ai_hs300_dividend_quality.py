"""HS300 TTM 分红增长 + 质量价值。

实验型分红增长策略。R89 的分红增长方向在 Discovery/Validation 不稳定，
本文件用于检验它和财务质量、低波动组合后的实际回测表现。
"""
import polars as pl

META = {
    "id": "ai_hs300_dividend_quality",
    "name": "HS300分红增长质量",
    "description": "TTM现金分红增长 + 质量价值 + 现金质量（实验）",
    "tags": ["分红", "股息", "质量", "价值", "沪深300", "实验"],
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
        "ext_alt_hs300_dividend_growth_ttm": 0.40,
        "uf_quality_value_defensive": 0.35,
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
    "ext_alt_hs300_dividend_growth_ttm",
}

RULES = """
1. 候选池为当前沪深300成分，使用公告日之后可见的 TTM 现金分红增长。
2. 评分 = 分红增长 40% + 质量价值防守 35% + 现金质量 25%。
3. 持有满 35 个交易日强制换出；不加固定止损。
4. 实验策略；分红方向不稳定时必须保留 Validation 结果。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    growth = pl.col("ext_alt_hs300_dividend_growth_ttm")
    return pl.col("ma60").is_not_null() & growth.is_not_null()
