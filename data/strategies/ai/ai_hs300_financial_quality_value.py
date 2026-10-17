"""HS300 财务质量价值组合。

把新增的质量价值防守因子和现金质量因子做成独立的可回测策略，
不依赖事件或外部另类数据，作为纯财报维度的对照腿。
"""
import polars as pl

META = {
    "id": "ai_hs300_financial_quality_value",
    "name": "HS300财务质量价值",
    "description": "质量价值防守 + 现金质量 + 低波动（实验）",
    "tags": ["财务", "质量", "价值", "现金流", "低波动", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.2e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "uf_quality_value_defensive": 0.45,
        "uf_cash_quality": 0.35,
        "uf_low_vol": 0.20,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35

REQUIRED_FEATURES = {"ma60"}

RULES = """
1. 候选池为当前沪深300成分，财务因子按公告日之后 PIT 生效。
2. 评分 = 质量价值防守 45% + 现金质量 35% + 低波动 20%。
3. 持有满 35 个交易日强制换出；不加固定止损。
4. 实验策略，作为财报维度的独立对照腿。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
