"""R271 research-only half-shrink industry-neutral variant of the A sleeve."""

import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_r271_industry_shrink05",
    "name": "R271 HS300宏观三闸门质量(行业中性收缩0.5)",
    "description": "R271探索候选：原宏观三闸门和评分权重，系统行业评分中性化 shrink=0.5；仅研究，不作部署。",
    "tags": ["R271", "行业中性", "shrink=0.5", "研究"],
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
    "neutralize": {"industry": "industry_l1", "shrink": 0.5},
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
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. ERP、信用利差、期限利差同时满足时允许开新仓。
2. 原宏观三闸门质量评分权重保持不变，评分通过系统现有行业中性化逻辑并以 shrink=0.5 收缩。
3. 持有满 35 个交易日换出；已有持仓不因宏观闸门变化立即卖出。
4. 仅供研究观察；行业分类为当前快照，且评分中性化不等于组合行业中性。
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
