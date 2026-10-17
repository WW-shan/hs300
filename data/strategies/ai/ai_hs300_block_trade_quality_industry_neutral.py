"""HS300 大宗交易质量 — 行业中性化实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_block_trade_quality_industry_neutral",
    "name": "HS300大宗交易质量(行业中性)",
    "description": "低大宗成交占比 + 高溢价 + 质量，评分做行业中性化（实验）",
    "tags": ["大宗交易", "折溢价", "事件", "质量", "行业中性", "沪深300", "实验"],
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
    "neutralize": {"industry": "industry_l1", "shrink": 1.0},
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
2. 评分腿与大宗交易质量原版相同，但在行业内部去均值并标准化。
3. 持有 20 个交易日换出；3× 成本全周期为正，但近一年仅 2/5 正收益，只保留实验。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_block_trade_ratio_20d_low") < 0)
    )
