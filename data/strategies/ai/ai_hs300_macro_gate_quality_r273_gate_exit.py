"""R273 research-only A-sleeve variant: exit positions on macro-gate closure."""

import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_r273_gate_exit",
    "name": "R273 HS300宏观三闸门质量(关闭后离场)",
    "description": "研究候选：保持R266 A腿入场与评分不变，宏观任一闸门关闭后在T+1开盘退出已有持仓。",
    "tags": ["R273", "宏观闸门", "退出规则", "研究"],
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
    "params": [
        {"id": "erp_min", "label": "ERP最低阈值", "type": "float", "default": 5.5, "min": 3.0, "max": 9.0, "step": 0.1},
        {"id": "credit_max", "label": "信用利差最高阈值", "type": "float", "default": 0.8, "min": 0.2, "max": 1.5, "step": 0.05},
        {"id": "term_min", "label": "期限利差最低阈值", "type": "float", "default": 0.3, "min": 0.0, "max": 1.5, "step": 0.05},
    ],
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
EXIT_SIGNALS = [
    "csg_r273_macro_exit_erp",
    "csg_r273_macro_exit_credit",
    "csg_r273_macro_exit_term",
]
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 入场过滤、三闸门、评分字段及权重与R266相同。
2. ERP < 5.5、信用利差因子 < -0.8、期限利差 < 0.3 中任一成立时产生退出信号。
3. 退出信号按系统T+1开盘撮合；停牌、跌停等不可成交约束保持不变。
4. 宏观闸门关闭禁止新开仓，并触发已有持仓退出；最大持有仍为35日。
5. 仅供研究，不代表纸面交易或生产建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
    )
