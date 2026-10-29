"""R274 research-only ablation: remove the three macro entry gates."""

import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_r274_no_macro_gate",
    "name": "R274 HS300宏观入场闸门消融",
    "description": "研究候选：保留R266基础过滤、评分和持有规则，仅移除ERP、信用利差、期限利差三项入场门槛。",
    "tags": ["R274", "宏观闸门消融", "研究"],
    "research_only": True,
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
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
        "uf_cash_quality": 0.20,
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
    "ext_alt_hs300_holder_top10_free_float_share",
}
RULES = """
1. 保留R266的基础股票过滤、评分字段和权重、排序、历史HS300范围与35日最大持有。
2. 唯一策略变化是移除ERP、信用利差、期限利差三项宏观入场过滤。
3. 不添加宏观退出信号；系统T+1开盘撮合、费用、滑点和不可成交约束不变。
4. 仅供研究，不代表纸面交易或生产建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
    )
