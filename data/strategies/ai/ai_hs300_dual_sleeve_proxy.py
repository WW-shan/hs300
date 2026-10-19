"""HS300 双袖近似 V2：B 腿评分 + 宏观三闸门硬过滤（R147 实验版）。

用 B 腿（行业相对强弱质量）评分选股，但仅在宏观三闸门同时满足时开新仓；
闸门关闭时不开仓（已有持仓按 20 日/止损规则离场）。这是双袖候选的
单策略近似，不等价于组合层风险平价 + 波动率目标。
"""
import polars as pl

META = {
    "id": "ai_hs300_dual_sleeve_proxy",
    "name": "HS300双袖近似(B腿+宏观闸门)",
    "description": "B 腿评分 + 宏观三闸门硬过滤（双袖候选的单策略近似，实验）",
    "tags": ["双袖近似", "宏观闸门", "行业相对强弱", "沪深300", "实验"],
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
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_equity_risk_premium", "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 评分与 B 腿相同（行业相对强弱 35% + 行业动量 15% + 质量 30% + 现金 20%）。
2. 仅当 ERP/信用/期限三闸门同时满足时允许开新仓；闸门关闭时不开仓。
3. 持有 20 日；固定 -20% 止损。
4. 这是双袖候选的单策略近似，不含组合层风险平价与波动率目标。
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
