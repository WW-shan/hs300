"""HS300 行业内相对强弱质量 — 20日持有 + 20%固定止损实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20",
    "name": "HS300行业内相对强弱质量(20日/止损20%)",
    "description": "R101行业相对强弱质量评分，20日持有并用-20%固定止损控制回撤（实验）",
    "tags": ["行业中性", "相对强弱", "质量", "止损", "风控", "沪深300", "实验"],
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
    "ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d",
}
RULES = """
1. 评分与 R101 行业内相对强弱质量策略相同。
2. 持有期 20 日；固定 -20% 止损来自预注册五点扫描。
3. -20% 止损将全周期最大回撤从约 -50.6% 降到 -40.9%，收益基本保留。
4. 行业分类为当前快照回填，存在行业回填偏差；仍为实验策略。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
