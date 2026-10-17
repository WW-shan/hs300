"""HS300 低基金拥挤度 + 财务质量。

基金持仓维度在 R60/R61 已证明单独使用不稳定；本策略只检验一个新的组合
假设：低基金覆盖、低基金持股比例，再叠加质量价值与现金质量。基金季报
数据在 `quarter_date + 45d` 后才进入扩展表，避免未来数据。
"""
import polars as pl

META = {
    "id": "ai_hs300_low_fund_crowding_quality",
    "name": "HS300低基金拥挤质量",
    "description": "低基金覆盖 + 低基金持股比例 + 质量价值 + 现金质量（实验）",
    "tags": ["基金持仓", "拥挤度", "质量", "价值", "沪深300", "实验"],
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
1. 使用季度末后 45 天才可得的基金持仓数据，不把未来披露回填到历史。
2. 评分 = 低基金覆盖 30% + 低基金持股比例 30% + 持股比例下降 15%
   + 质量价值 15% + 现金质量 10%。
3. 只要求行情预热和基金持仓水平可用；季度变化缺失时不做零填充。
4. 持有满 35 个交易日换出；本策略是组合实验，不单独宣称基金持仓 alpha。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_ratio_low").is_not_null()
    )
