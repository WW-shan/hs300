"""HS300 基金减仓质量 — 行业中性化实验版。

R98 发现基金减仓组合在行业中性后长周期表现改善，因此单独落成策略文件，
但近一年仍然为负，不能据此宣称可部署。
"""
import polars as pl

META = {
    "id": "ai_hs300_fund_reduction_quality_industry_neutral",
    "name": "HS300基金减仓质量(行业中性)",
    "description": "基金家数/持股比例下降 + 质量，评分做行业中性化（实验）",
    "tags": ["基金持仓", "减仓", "拥挤度", "质量", "行业中性", "沪深300", "实验"],
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
        "ext_alt_hs300_fund_count_chg_qoq_low": 0.35,
        "ext_alt_hs300_fund_holding_ratio_chg_qoq_low": 0.25,
        "ext_alt_hs300_fund_holding_value_chg_qoq_low": 0.15,
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
    "ext_alt_hs300_fund_count_chg_qoq_low",
    "ext_alt_hs300_fund_holding_ratio_chg_qoq_low",
    "ext_alt_hs300_fund_holding_value_chg_qoq_low",
}
RULES = """
1. 只使用季度末后 45 天已经可见的基金持仓变化。
2. 评分腿与基金减仓质量原版相同，但在行业内部去均值并逐行业标准化。
3. 三个季度变化都必须可用，缺失时不参与排序。
4. 持有满 35 个交易日换出；近一年证据仍弱，保持实验状态。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_chg_qoq_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_ratio_chg_qoq_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_value_chg_qoq_low").is_not_null()
    )
