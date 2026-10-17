"""HS300 低质押质量 — 行业中性化实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_pledge_quality_industry_neutral",
    "name": "HS300低质押质量(行业中性)",
    "description": "低质押比例/质押下降 + 质量，评分做行业中性化（实验）",
    "tags": ["质押", "风险", "质量", "行业中性", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_pledge_ratio_low": 0.40,
        "ext_alt_hs300_pledge_ratio_chg_1m_low": 0.20,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.15,
    },
    "neutralize": {"industry": "industry_l1", "shrink": 1.0},
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_pledge_ratio_low",
    "ext_alt_hs300_pledge_ratio_chg_1m_low",
}
RULES = """
1. 质押快照后下一自然日生效，超过 14 天不使用；无快照不补 0。
2. 评分腿与低质押质量原版相同，但在行业内部去均值并标准化。
3. 持有 35 个交易日换出；3× 成本下全周期仍为负，只保留实验。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_pledge_ratio_low").is_not_null()
    )
