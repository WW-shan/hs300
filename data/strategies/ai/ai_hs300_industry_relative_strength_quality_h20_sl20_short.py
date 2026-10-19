"""HS300 行业内相对强弱质量 + 低融券拥挤度倾斜（R183 实验版）。

在 R102d 的 B 腿（20日持有 + 20%止损）基础上，把 10% 权重倾斜给
R181 全过门槛的 `ext_alt_hs300_short_ratio_low`（融券余量/流通股本反向，
低空头拥挤）。其余 90% 权重按原比例缩放。
"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_short",
    "name": "HS300行业相对强弱质量(20日/止损20%/融券拥挤倾斜)",
    "description": "R102d B 腿 + 10% 低融券拥挤度倾斜（R181 全过预注册门槛因子，实验）",
    "tags": ["行业内中性", "相对强弱", "质量", "止损", "融券", "倾斜", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.315,
        "ext_alt_hs300_industry_mom_20d": 0.135,
        "uf_quality_value_defensive": 0.27,
        "uf_cash_quality": 0.18,
        "ext_alt_hs300_short_ratio_low": 0.10,
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
1. 评分 = 原 B 腿四项按 90% 缩放 + 融券余量占流通股本反向 10%。
2. 持有 20 日；固定 -20% 止损。
3. R181 显示该因子 Discovery/Validation 双段 IC 同号且全过门槛（+0.104/+0.072）。
4. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
