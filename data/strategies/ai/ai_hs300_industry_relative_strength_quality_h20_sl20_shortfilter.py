"""HS300 行业内相对强弱质量 + 融券拥挤度硬过滤（R185 实验版）。

评分与执行与 R102d B 腿完全一致（四项权重、20 日持有、-20% 止损），
只在选股阶段剔除融券拥挤度最高的 20%（`ext_alt_hs300_short_ratio_low`
的当月截面 20% 分位以下），不改变评分权重。
"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_shortfilter",
    "name": "HS300行业相对强弱质量(20日/止损20%/融券硬过滤)",
    "description": "R102d B 腿 + 剔除融券拥挤度最高 20% 的硬过滤（R181 因子，实验）",
    "tags": ["行业内中性", "相对强弱", "质量", "止损", "融券", "硬过滤", "沪深300", "实验"],
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
    "ext_alt_hs300_short_ratio_low",
}
RULES = """
1. 评分与 R102d B 腿完全相同（四项权重不变）。
2. 选股硬过滤：剔除融券拥挤度最高的 20%（short_ratio_low 低于当月截面 20% 分位）。
3. 持有 20 日；固定 -20% 止损。
4. R181 显示该因子 Discovery/Validation 双段 IC 同号且全过门槛（+0.104/+0.072）。
5. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & pl.col("ext_alt_hs300_short_ratio_low").is_not_null()
        & (pl.col("ext_alt_hs300_short_ratio_low") >= pl.col("ext_alt_hs300_short_ratio_low").quantile(0.20))
    )
