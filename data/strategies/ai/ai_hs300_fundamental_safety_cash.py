"""HS300 财报安全现金复合策略。"""
import polars as pl

META = {
    "id": "ai_hs300_fundamental_safety_cash",
    "name": "HS300财报安全现金",
    "description": "低负债/OCF覆盖负债/现金含量/低应计/毛利率复合（实验）",
    "tags": ["财报", "安全", "杠杆", "现金流", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_fundamental_safety_cash": 0.75,
        "uf_low_vol": 0.25,
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
2. 评分 = 财报安全现金75% + 低波动25%。
3. 35日持有、无固定止损；财报安全维度必须单独接受近一年检验。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
