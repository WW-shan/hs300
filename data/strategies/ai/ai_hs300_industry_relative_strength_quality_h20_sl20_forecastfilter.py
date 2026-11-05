"""HS300 行业内相对强弱质量 — 负向业绩预告尾部过滤实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_forecastfilter",
    "name": "HS300行业内相对强弱质量(20日/止损20%/负向预告过滤)",
    "description": "R102 行业内相对强弱质量评分和交易规则不变，仅剔除近60日负向业绩预告 (实验)",
    "tags": ["行业中性", "相对强弱", "质量", "止损", "业绩预告", "尾部过滤", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
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
    "ma60",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_forecast_surprise_60d",
}
RULES = """
1. 原 B 腿评分、20 日持有和 -20% 止损完全不变。
2. 仅增加尾部过滤：近 60 日业绩预告 surprise 为负的股票不参与新开仓；无事件行值为 0，允许通过。
3. 不改权重、持有期、止损、成本或行业口径。
4. 实验策略；负向预告过滤作为风险过滤，不宣称业绩预告本身是正向 alpha。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & pl.col("ext_alt_hs300_forecast_surprise_60d").is_not_null()
        & (pl.col("ext_alt_hs300_forecast_surprise_60d") >= 0)
    )
