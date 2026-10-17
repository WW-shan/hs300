"""HS300 低股权质押 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_pledge_quality",
    "name": "HS300低质押质量",
    "description": "低质押比例/质押下降 + 质量价值 + 现金质量（实验）",
    "tags": ["质押", "风险", "质量", "沪深300", "实验"],
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
1. 使用月度质押快照，快照后下一自然日生效，超过 14 天不使用。
2. 评分 = 低质押比例 40% + 质押下降 20% + 质量 25% + 现金质量 15%。
3. 无质押快照不补 0，不参与排序。
4. 持有 35 个交易日换出；质押维度单独研究未过完整门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_pledge_ratio_low").is_not_null()
    )
