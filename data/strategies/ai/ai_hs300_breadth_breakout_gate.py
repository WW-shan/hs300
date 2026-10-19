"""HS300 新高/新低宽度择时（R197 实验版）。

月末读取 HS300 成分中"20 日新高比例 - 20 日新低比例"的扩张 z-score
（`ext_alt_hs300_nh_nl_diff_z`，方向正：值 >0 表示上涨趋势广度占优）。
信号 >0 时持有等权 HS300 成分，否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_breadth_breakout_gate",
    "name": "HS300 新高新低宽度择时",
    "description": "HS300 成分 20 日新高减新低比例 z-score >0 时持有 HS300，否则空仓（R197 实验）",
    "tags": ["宽度", "新高新低", "突破", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_nh_nl_diff_z": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_nh_nl_diff_z",
}
RULES = """
1. 信号 = HS300 成分 20 日新高比例减新低比例的扩张 z-score，方向正。
2. 信号 >0（上涨趋势广度占优）时下一月持有等权 HS300 成分；否则空仓。
3. 月末生成信号，T+1 开盘调仓，持有 20 个交易日。
4. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """Monthly signal: only enter on the last trading day of each month."""
    del df, params
    next_day = pl.col("date") + pl.duration(days=1)
    is_month_end = next_day.dt.month() != pl.col("date").dt.month()
    return (
        is_month_end
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_nh_nl_diff_z").is_not_null()
        & (pl.col("ext_alt_hs300_nh_nl_diff_z") > 0)
    )
