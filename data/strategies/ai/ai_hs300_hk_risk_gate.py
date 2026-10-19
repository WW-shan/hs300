"""HS300 港股风险偏好联动择时（R195 实验版）。

月末读取恒生指数 20 日动量（`ext_alt_hs300_hsi_mom_20d`，方向正：值 >0
表示港股趋势走强/境外风险偏好改善）。信号 >0 时持有等权 HS300 成分，
否则空仓。信号为市场级，广播到所有标的。
"""
import polars as pl

META = {
    "id": "ai_hs300_hk_risk_gate",
    "name": "HS300 港股风险偏好联动择时",
    "description": "恒生指数 20 日动量 >0（港股趋势走强）时持有 HS300，否则空仓（R195 实验）",
    "tags": ["恒生指数", "港股", "风险偏好", "动量", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_hsi_mom_20d": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_hsi_mom_20d",
}
RULES = """
1. 信号 = 恒生指数 20 日价格动量，方向正。
2. 信号 >0（港股趋势走强/境外风险偏好改善）时下一月持有等权 HS300 成分；否则空仓。
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
        & pl.col("ext_alt_hs300_hsi_mom_20d").is_not_null()
        & (pl.col("ext_alt_hs300_hsi_mom_20d") > 0)
    )
