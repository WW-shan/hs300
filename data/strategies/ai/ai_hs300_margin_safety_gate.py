"""HS300 两融维持担保比例风险规避择时（R196 实验版）。

月末读取平均维持担保比例的扩张 z-score（`ext_alt_hs300_margin_safety_z`，
方向正：值 >0 表示杠杆结构比历史更安全）。信号 >0 时持有等权 HS300 成分，
否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_margin_safety_gate",
    "name": "HS300 两融维持担保比例择时",
    "description": "两融维持担保比例 z-score >0（杠杆结构更安全）时持有 HS300，否则空仓（R196 实验）",
    "tags": ["两融", "维持担保比例", "风险规避", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_margin_safety_z": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_margin_safety_z",
}
RULES = """
1. 信号 = 两融平均维持担保比例的扩张 z-score，方向正。
2. 信号 >0（杠杆结构比历史更安全）时下一月持有等权 HS300 成分；否则空仓。
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
        & pl.col("ext_alt_hs300_margin_safety_z").is_not_null()
        & (pl.col("ext_alt_hs300_margin_safety_z") > 0)
    )
