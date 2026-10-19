"""HS300 SHIBOR 3M 流动性择时（R198 实验版）。

月末读取 SHIBOR 3M 的扩张 z-score（`ext_alt_hs300_shibor_tightness_z`，
方向负：值 <0 表示银行间资金成本低于历史均值/流动性偏松）。信号 <0 时
持有等权 HS300 成分，否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_shibor_liquidity_gate",
    "name": "HS300 SHIBOR流动性择时",
    "description": "SHIBOR 3M z-score <0（资金成本偏低/流动性宽松）时持有 HS300，否则空仓（R198 实验）",
    "tags": ["SHIBOR", "流动性", "利率", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_shibor_tightness_z": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_shibor_tightness_z",
}
RULES = """
1. 信号 = SHIBOR 3M 的扩张 z-score，方向负。
2. 信号 <0（资金成本低于历史均值/流动性宽松）时下一月持有等权 HS300 成分；否则空仓。
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
        & pl.col("ext_alt_hs300_shibor_tightness_z").is_not_null()
        & (pl.col("ext_alt_hs300_shibor_tightness_z") < 0)
    )
