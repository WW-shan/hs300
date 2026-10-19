"""HS300 国债期货动量择时（R193 实验版）。

月末读取 10Y 国债期货 20 日动量（`ext_alt_hs300_bond_mom_20d`，方向负：
值 <0 表示债券期货下跌/收益率上行）。信号 <0 时持有等权 HS300 成分，
否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_bond_momentum_gate",
    "name": "HS300 国债期货动量择时",
    "description": "10Y 国债期货 20 日动量 <0（收益率上行）时持有 HS300，否则空仓（R193 实验）",
    "tags": ["国债期货", "利率", "动量", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_bond_mom_20d": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_bond_mom_20d",
}
RULES = """
1. 信号 = T0（10Y 国债期货）20 日价格动量，方向负。
2. 信号 <0（债券期货下跌/收益率上行）时下一月持有等权 HS300 成分；否则空仓。
3. 月度月末生成信号，T+1 开盘调仓，持有 20 个交易日。
4. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """Monthly signal: only enter on the last trading day of each month.

    The ext field is broadcast daily; the month-end gate keeps the strategy
    aligned with the pre-registered monthly signal.
    """
    del params
    next_day = pl.col("date") + pl.duration(days=1)
    is_month_end = next_day.dt.month() != pl.col("date").dt.month()
    return (
        is_month_end
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_bond_mom_20d").is_not_null()
        & (pl.col("ext_alt_hs300_bond_mom_20d") < 0)
    )
