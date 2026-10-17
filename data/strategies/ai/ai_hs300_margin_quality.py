"""HS300 两融杠杆变化 + 财务质量 + 低波动。"""
import polars as pl

META = {
    "id": "ai_hs300_margin_quality",
    "name": "HS300两融质量",
    "description": "两融余额月度变化反向 + 质量价值 + 低波动（实验）",
    "tags": ["两融", "杠杆", "质量", "低波动", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_margin_chg_1m_low": 0.40,
        "uf_quality_value_defensive": 0.30,
        "uf_low_vol": 0.30,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_margin_chg_1m_low"}
RULES = """
1. 两融余额月度变化为公告/月末快照后下一日可用；越低越好。
2. 评分 = 两融变化反向 40% + 质量价值 30% + 低波动 30%。
3. 持有35个交易日强制换出；不加固定止损。
4. 实验策略；R50 的 F2 分组单调性未过。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_margin_chg_1m_low").is_not_null()
