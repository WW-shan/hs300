"""HS300 财报增长 + 安全现金组合策略。"""
import polars as pl

META = {
    "id": "ai_hs300_fundamental_growth_safety_combo",
    "name": "HS300财报增长安全组合",
    "description": "财报增长质量与安全现金两条复合腿 + 低波动（实验）",
    "tags": ["财报", "成长", "安全", "现金流", "低波动", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_fundamental_growth_quality": 0.40,
        "cf_fundamental_safety_cash": 0.40,
        "uf_low_vol": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {"ma60"}
RULES = """
1. 财报因子按公告日之后 PIT 生效。
2. 评分 = 增长质量40% + 安全现金40% + 低波动20%。
3. 35日持有、无固定止损；组合更多不自动更好。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
