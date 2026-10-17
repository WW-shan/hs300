"""HS300 大宗交易结构 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_block_trade_quality",
    "name": "HS300大宗交易质量",
    "description": "低大宗成交占比 + 高成交溢价 + 质量价值 + 低波动（实验）",
    "tags": ["大宗交易", "折溢价", "事件", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_block_trade_ratio_20d_low": 0.35,
        "ext_alt_hs300_block_premium_20d": 0.25,
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
    "ma60",
    "ext_alt_hs300_block_trade_ratio_20d_low",
    "ext_alt_hs300_block_premium_20d",
}
RULES = """
1. 大宗交易公告后下一交易日生效，20 个交易日内线性衰减。
2. 只保留近期有大宗交易的股票。
3. 评分 = 低成交占比 35% + 高溢价 25% + 质量 25% + 低波动 15%。
4. 持有 20 个交易日换出；大宗交易维度此前未过完整研究门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_block_trade_ratio_20d_low") < 0)
    )
