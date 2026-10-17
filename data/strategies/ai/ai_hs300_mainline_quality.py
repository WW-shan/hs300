"""HS300 主线行业/龙头 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_mainline_quality",
    "name": "HS300主线质量",
    "description": "主线行业第一/强度/龙头 + 质量 + 低波动（实验）",
    "tags": ["主线", "行业", "龙头", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_mainline_top1_flag": 0.25,
        "ext_alt_hs300_mainline_strength": 0.20,
        "ext_alt_hs300_mainline_leader_flag": 0.15,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_mainline_top1_flag",
    "ext_alt_hs300_mainline_strength", "ext_alt_hs300_mainline_leader_flag",
}
RULES = """
1. mainline_history 只有 127 个有记录交易日，缺失日期不补造。
2. 只在当日存在主线第一行业时允许开新仓。
3. 评分 = 主线行业 25% + 主线强度 20% + 龙头 15% + 质量 25% + 低波动 15%。
4. 持有 20 个交易日换出；主线数据已停更，只能保留实验。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_mainline_top1_flag") > 0)
    )
