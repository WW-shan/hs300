"""R149 V3：仅质量。"""
import polars as pl

META = {
    "id": "ai_hs300_b_decomp_quality_only",
    "name": "HS300 B分解-仅质量",
    "description": "仅质量+现金 50/50（B 腿机制分解 V3，实验）",
    "tags": ["机制分解", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8, "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60},
    "params": [],
    "scoring": {"uf_quality_value_defensive": 0.5, "uf_cash_quality": 0.5},
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60"}
RULES = "1. 评分 = 质量 50% + 现金 50%。2. 持有 20 日；-20% 止损。"


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
