"""HS300 低基金拥挤质量 — 行业中性化实验版。

R98 显示低基金拥挤组合的收益带有明显行业暴露；本文件把同一评分腿在
行业内部去均值并标准化，检验行业中性后的组合是否仍有稳定收益。
"""
import polars as pl

META = {
    "id": "ai_hs300_low_fund_crowding_quality_industry_neutral",
    "name": "HS300低基金拥挤质量(行业中性)",
    "description": "低基金覆盖 + 低基金持股比例 + 质量，评分做行业中性化（实验）",
    "tags": ["基金持仓", "拥挤度", "质量", "行业中性", "沪深300", "实验"],
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
        "ext_alt_hs300_fund_count_low": 0.30,
        "ext_alt_hs300_fund_holding_ratio_low": 0.30,
        "ext_alt_hs300_fund_holding_ratio_chg_qoq_low": 0.15,
        "uf_quality_value_defensive": 0.15,
        "uf_cash_quality": 0.10,
    },
    "neutralize": {"industry": "industry_l1", "shrink": 1.0},
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
    "ext_alt_hs300_fund_count_low",
    "ext_alt_hs300_fund_holding_ratio_low",
    "ext_alt_hs300_fund_holding_ratio_chg_qoq_low",
}
RULES = """
1. 基金持仓在季度末后 45 天才可见，不能提前使用。
2. 评分腿与低基金拥挤质量原版相同，但在行业内部去均值并逐行业标准化。
3. 持有满 35 个交易日换出；行业中性版本只作为 R98 稳健性实验。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_ratio_low").is_not_null()
    )
