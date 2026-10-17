"""HS300 股权融资供给反向 + 财务质量 + 低波动。"""
import polars as pl

META = {
    "id": "ai_hs300_supply_quality",
    "name": "HS300股权供给质量",
    "description": "180日股权融资供给反向 + 质量价值 + 低波动（实验）",
    "tags": ["增发", "配股", "可转债", "股权供给", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_equity_supply_ratio_180d_low": 0.40,
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
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_equity_supply_ratio_180d_low"}
RULES = """
1. 增发/配股/可转债转股按公告日之后进入因子，过去180日累计稀释越小越好。
2. 评分 = 股权供给反向 40% + 质量价值 30% + 低波动 30%。
3. 持有35个交易日强制换出；不加固定止损。
4. 实验策略；R68 的 IC 方向为正但未过 F1 门槛。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_equity_supply_ratio_180d_low").is_not_null()
