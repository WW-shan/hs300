"""HS300 基金减仓 + 财务质量。

R60 的基金持仓季度变化单独没有稳定 IC。本策略把“基金家数和持股比例同时
下降”作为低拥挤改善腿，再叠加质量与现金质量，检验组合层是否比单因子稳定。
"""
import polars as pl

META = {
    "id": "ai_hs300_fund_reduction_quality",
    "name": "HS300基金减仓质量",
    "description": "基金家数/持股比例季度下降 + 质量价值 + 现金质量（实验）",
    "tags": ["基金持仓", "减仓", "拥挤度", "质量", "沪深300", "实验"],
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
2. 评分 = 基金家数下降 35% + 持股比例下降 25% + 持股市值下降 15%
   + 质量价值 15% + 现金质量 10%。
3. 三个季度变化都必须可用，缺失时不参与排序。
4. 持有满 35 个交易日换出；实验策略，不能把减仓直接解释为利好。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_chg_qoq_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_ratio_chg_qoq_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_value_chg_qoq_low").is_not_null()
    )
