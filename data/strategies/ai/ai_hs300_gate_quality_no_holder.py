"""R148 变体 V5：闸门 + 质量评分（无 holder）。"""
import polars as pl

META = {
    "id": "ai_hs300_gate_quality_no_holder",
    "name": "HS300闸门质量无holder(机制分解V5)",
    "description": "宏观三闸门 + 质量评分（去掉 holder，其余按比例放大，实验）",
    "tags": ["机制分解", "宏观闸门", "质量", "沪深300", "实验"],
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
        "uf_quality_value_defensive": 0.385,
        "uf_low_vol": 0.308,
        "uf_cash_quality": 0.308,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_equity_risk_premium", "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 宏观三闸门同时满足时允许开新仓。
2. 评分 = 质量 38.5% + 低波 30.8% + 现金 30.8%（原 A 腿去掉 holder 后按比例放大）。
3. 持有 20 日；固定 -20% 止损。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
    )
