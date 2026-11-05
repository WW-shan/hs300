"""HS300 宏观三闸门质量 + 负向业绩预告尾部过滤实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_forecastfilter",
    "name": "HS300宏观三闸门质量(负向预告过滤)",
    "description": "宏观三闸门质量评分不变，仅剔除近60日负向业绩预告 (实验)",
    "tags": ["ERP", "信用利差", "期限利差", "宏观", "质量", "业绩预告", "尾部过滤", "实验"],
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
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
        "uf_cash_quality": 0.20,
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
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_forecast_surprise_60d",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 原 A 腿宏观三闸门与评分完全不变。
2. 仅增加尾部过滤：近 60 日业绩预告 surprise 为负的股票不参与新开仓；无事件行值为 0，允许通过。
3. 持有35个交易日强制换出；不改评分、持有期、成本或止损。
4. 实验策略；负向预告过滤作为风险过滤，不宣称业绩预告本身是正向 alpha。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
        & pl.col("ext_alt_hs300_forecast_surprise_60d").is_not_null()
        & (pl.col("ext_alt_hs300_forecast_surprise_60d") >= 0)
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
    )
