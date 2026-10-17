"""HS300 行业相对强弱 + 基金减仓 + 现金质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_fund_reduction_combo",
    "name": "HS300行业强弱基金减仓组合",
    "description": "行业内相对强弱叠加基金持仓下降、现金质量与质量价值防守（实验）",
    "tags": ["行业", "相对强弱", "基金持仓", "减仓", "拥挤度", "现金质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_industry_fund_reduction": 0.60,
        "ext_alt_hs300_industry_rps_20d": 0.15,
        "uf_low_vol": 0.25,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_rps_20d",
    "ext_alt_hs300_fund_count_chg_qoq_low",
    "ext_alt_hs300_fund_holding_ratio_chg_qoq_low",
}
RULES = """
1. 只使用季度末后 45 天已经可见的基金持仓变化。
2. 评分 = 行业强弱基金减仓复合 60% + 行业RPS 15% + 低波动 25%。
3. 基金减仓相关字段缺失时不参与排序；持有 35 个交易日换出。
4. R98 基金减仓组合近一年仍弱，本策略只检验与行业相对强弱的组合增量。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_chg_qoq_low").is_not_null()
        & pl.col("ext_alt_hs300_fund_holding_ratio_chg_qoq_low").is_not_null()
    )
