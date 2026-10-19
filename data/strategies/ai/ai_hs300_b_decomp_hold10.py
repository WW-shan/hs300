"""R149 V6：当前 B 腿但持有 10 日。"""
import polars as pl

META = {
    "id": "ai_hs300_b_decomp_hold10",
    "name": "HS300 B分解-持有10日",
    "description": "当前 B 腿评分但持有 10 日（B 腿机制分解 V6，实验）",
    "tags": ["机制分解", "相对强弱", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8, "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60},
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 10
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d"}
RULES = "1. 评分同 B 腿。2. 持有 10 日；-20% 止损。"


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
