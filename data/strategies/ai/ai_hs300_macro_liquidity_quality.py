"""HS300 M1-M2 流动性闸门 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_macro_liquidity_quality",
    "name": "HS300流动性质量",
    "description": "M1-M2剪刀差改善时持有质量/现金/低波动组合（实验）",
    "tags": ["M1", "M2", "流动性", "宏观", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_m1_m2_scissors_chg_1m": 0.20,
        "uf_quality_value_defensive": 0.35,
        "uf_cash_quality": 0.25,
        "uf_low_vol": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_m1_m2_scissors_chg_1m"}
RULES = """
1. M1/M2 数据按月份结束后 45 天保守可得，停更后不向前填充。
2. 只有 M1-M2 剪刀差环比改善时才允许开新仓。
3. 评分 = 流动性改善 20% + 质量 35% + 现金质量 25% + 低波动 20%。
4. 持有 35 个交易日换出；R27 宏观分层单独未达门槛，本策略只检验组合闸门。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_m1_m2_scissors_chg_1m") > 0)
    )
