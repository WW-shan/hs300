"""HS300 行业强弱现金质量 + 宏观三闸门实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_cash_macro_gate",
    "name": "HS300行业强弱现金质量宏观闸门",
    "description": "行业强弱现金质量复合，在ERP/信用/期限三闸门放行时持有（实验）",
    "tags": ["行业", "相对强弱", "现金质量", "ERP", "信用利差", "期限利差", "沪深300", "实验"],
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
        "cf_industry_cash_quality": 0.60,
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
    "ext_alt_hs300_industry_rps_20d", "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low", "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. ERP >= 阈值、信用利差 <= 阈值、期限利差 >= 阈值时才允许开新仓。
2. 评分 = 行业强弱现金质量复合 60% + 行业RPS 15% + 低波动 25%。
3. 已有持仓不因闸门变化立即卖出，持有满 35 个交易日换出。
4. 宏观单项均未过稳定门槛，本策略只检验与行业选股腿的组合。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
    )
