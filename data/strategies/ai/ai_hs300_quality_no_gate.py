"""R148 变体 V2：质量评分但无宏观闸门。"""
import polars as pl

META = {
    "id": "ai_hs300_quality_no_gate",
    "name": "HS300质量无闸门(机制分解V2)",
    "description": "质量评分（holder/质量/低波/现金）但去掉宏观闸门（实验）",
    "tags": ["机制分解", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_holder_top10_free_float_share"}
RULES = """
1. 无宏观闸门，始终允许开仓。
2. 评分 = holder 35% + 质量 25% + 低波 20% + 现金 20%。
3. 持有 20 日；固定 -20% 止损。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
    )
