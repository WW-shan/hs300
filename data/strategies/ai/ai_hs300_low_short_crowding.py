"""HS300 低融券拥挤度 long-only 实验策略（R184 第三袖）。

按 R181 全过门槛的 `ext_alt_hs300_short_ratio_low`（融券余量/流通股本反向）
排序选前 90 只，20 日持有、无止损。用于三袖组合增量检验。
"""
import polars as pl

META = {
    "id": "ai_hs300_low_short_crowding",
    "name": "HS300低融券拥挤度",
    "description": "融券余量占流通股本比例最低的 90 只，20日持有（R181 因子，实验）",
    "tags": ["融券", "拥挤度", "低风险", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_short_ratio_low": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 90,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_short_ratio_low",
}
RULES = """
1. 评分 = 融券余量占流通股本比例反向（越低越好）。
2. 选前 90 只等权，20 日持有换出，无固定止损。
3. R181 显示该因子 Discovery/Validation 双段 IC 同号且全过门槛（+0.104/+0.072）。
4. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_short_ratio_low").is_not_null()
    )
