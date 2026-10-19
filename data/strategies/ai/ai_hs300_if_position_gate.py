"""HS300 IF 持仓排名净空头变化择时（R188 实验版）。

月末读取 IF 前 20 席位净空头占比的月度变化（`ext_alt_hs300_if_net_short_chg_1m`，
字段值 = -(净空头占比(T) - 净空头占比(T-1))，方向负：值 >0 表示净空头下降/转多）。
信号 >0 时持有等权 HS300 成分，否则空仓。信号为市场级，广播到所有标的。

2026-10-19 修正：首版过滤条件误写为 ext < 0（等价于净空头增加时持仓），与 R187
预注册方向相反；standalone 结论作废并重跑。组合叠加部分（R188 脚本内直接构造
信号）未受影响。
"""
import polars as pl

META = {
    "id": "ai_hs300_if_position_gate",
    "name": "HS300 IF持仓净空头变化择时",
    "description": "IF 前 20 席位净空头占比月度变化 >0（净空头下降）时持有 HS300，否则空仓（R187 全过因子，实验）",
    "tags": ["IF", "持仓排名", "择时", "空头拥挤", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_if_net_short_chg_1m": 1.0,
    },
    "order_by": "score", "descending": True, "limit": 300,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_if_net_short_chg_1m",
}
RULES = """
1. 信号 = IF 前 20 席位净空头占比月度变化取负（>0 表示净空头下降/转多）。
2. 信号 >0 时下一月持有等权 HS300 成分；否则空仓。
3. R187 显示该信号 Discovery/Validation/Holdout 三段方向化 IC 同号且全过门槛。
4. 实验策略；不构成部署建议。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_if_net_short_chg_1m").is_not_null()
        & (pl.col("ext_alt_hs300_if_net_short_chg_1m") > 0)
    )
