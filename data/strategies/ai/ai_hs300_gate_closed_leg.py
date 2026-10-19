"""HS300 闸门关闭期第三腿（R155 实验版）。

仅在宏观三闸门关闭时开仓，用 R154 发现的关闭期专属因子：
基金持仓家数环比下降 30% + 业绩预告超预期 25% + 两融余额下降 25% + 行业动量 20%。
持有 20 日；止损 -20%。
"""
import polars as pl

META = {
    "id": "ai_hs300_gate_closed_leg",
    "name": "HS300闸门关闭腿",
    "description": "仅闸门关闭时开仓，用关闭期专属因子（R154 发现，实验）",
    "tags": ["第三腿", "闸门关闭", "关闭期因子", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [
        {"id": "erp_min", "label": "ERP最低阈值", "type": "float", "default": 5.5, "min": 3.0, "max": 9.0, "step": 0.1},
        {"id": "credit_max", "label": "信用利差最高阈值", "type": "float", "default": 0.8, "min": 0.2, "max": 1.5, "step": 0.05},
        {"id": "term_min", "label": "期限利差最低阈值", "type": "float", "default": 0.3, "min": 0.0, "max": 1.5, "step": 0.05},
    ],
    "scoring": {
        "ext_alt_hs300_fund_count_chg_qoq_low": 0.30,
        "ext_alt_hs300_forecast_surprise_60d": 0.25,
        "ext_alt_hs300_margin_chg_1m_low": 0.25,
        "ext_alt_hs300_industry_mom_20d": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_fund_count_chg_qoq_low", "ext_alt_hs300_forecast_surprise_60d",
    "ext_alt_hs300_margin_chg_1m_low", "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_equity_risk_premium", "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 仅在宏观三闸门关闭时允许开新仓（与 A 腿互补）。
2. 评分 = 基金持仓家数环比下降 30% + 业绩预告超预期 25% + 两融余额下降 25% + 行业动量 20%。
3. 持有 20 日；固定 -20% 止损。
4. 实验策略；关闭期样本仅 23 个月，统计力有限。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    gate_open = (
        (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
    )
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fund_count_chg_qoq_low").is_not_null()
        & (~gate_open)
    )
