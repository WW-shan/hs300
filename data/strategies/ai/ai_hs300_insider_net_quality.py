"""HS300 董监高净增持事件 + 减持规避 + 财务质量。"""
import polars as pl

META = {
    "id": "ai_hs300_insider_net_quality",
    "name": "HS300内部人净增持质量",
    "description": "董监高净增持 + 减持事件反向 + 质量价值 + 低波动（实验）",
    "tags": ["增持", "减持", "内部人", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_insider_net_buy_20d": 0.40,
        "ext_alt_hs300_insider_sell_20d_low": 0.15,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.10
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_insider_net_buy_20d",
    "ext_alt_hs300_insider_sell_20d_low",
}
RULES = """
1. 只保留公告后20个交易日内净增持为正的股票。
2. 评分 = 净增持强度 40% + 减持反向 15% + 质量价值 25% + 低波动 20%。
3. 公告日当天不建仓，持有20个交易日或 -10% 止损。
4. 实验策略；R90 的增持事件在验证段中位收益转负。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    net_buy = pl.col("ext_alt_hs300_insider_net_buy_20d")
    return pl.col("ma60").is_not_null() & net_buy.is_not_null() & (net_buy > 0)
