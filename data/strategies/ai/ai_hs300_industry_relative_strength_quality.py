"""HS300 行业内相对强弱 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality",
    "name": "HS300行业内相对强弱质量",
    "description": "个股相对行业20日强弱 + 行业动量 + 质量 + 现金质量（实验）",
    "tags": ["行业中性", "相对强弱", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d",
}
RULES = """
1. 行业内相对强弱 = 个股20日收益 - 所属行业等权20日收益。
2. 评分 = 相对强弱 35% + 行业动量 15% + 质量 30% + 现金质量 20%。
3. 持有 35 个交易日换出；不额外加行业中性化，因为核心腿已经是行业内相对量。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
