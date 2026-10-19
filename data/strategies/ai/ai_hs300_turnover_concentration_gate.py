"""HS300 成交额集中度择时（R199 实验版）。

月末读取 HS300 成交额 HHI 的扩张 z-score（`ext_alt_hs300_turnover_hhi_z`，
方向负：值 <0 表示成交额分布更分散/参与更广泛）。信号 <0 时持有等权
HS300 成分，否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_turnover_concentration_gate",
    "name": "HS300 成交额集中度择时",
    "description": "HS300 成交额 HHI z-score <0（成交分散/参与广泛）时持有 HS300，否则空仓（R199 实验）",
    "tags": ["成交额", "集中度", "HHI", "拥挤", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_turnover_hhi_z": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_turnover_hhi_z",
}
RULES = """
1. 信号 = HS300 成交额 HHI 的扩张 z-score，方向负。
2. 信号 <0（成交分散/参与广泛）时下一月持有等权 HS300 成分；否则空仓。
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
        & pl.col("ext_alt_hs300_turnover_hhi_z").is_not_null()
        & (pl.col("ext_alt_hs300_turnover_hhi_z") < 0)
    )
