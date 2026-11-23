"""R275 single-gate ablation: remove only the credit-spread entry condition."""

import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_r275_remove_credit",
    "name": "R275 宏观门禁消融 - 信用利差",
    "description": "研究候选：保留 ERP 与期限利差门禁，只移除信用利差入场门禁。",
    "tags": ["R275", "信用利差", "研究"],
    "research_only": True,
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
        "uf_cash_quality": 0.20,
    },
    "params": [],
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_term_spread_10y_2y",
}


RULES = """
1. 候选池要求 MA60 与股东集中度因子有效。
2. 开仓要求 ERP 不低于 5.5、期限利差不低于 0.3，仅移除信用利差门禁。
3. 评分由股东集中度、质量价值、低波与现金质量构成；持有 35 个交易日。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= 5.5)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= 0.3)
    )
