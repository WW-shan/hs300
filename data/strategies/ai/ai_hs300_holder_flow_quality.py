"""HS300 重要股东净增持 + 前十大流通股东变化 + 财务质量。"""
import polars as pl

META = {
    "id": "ai_hs300_holder_flow_quality",
    "name": "HS300股东资金流质量",
    "description": "重要股东90日净增持 + 前十大流通股东环比变化 + 质量价值/现金质量（实验）",
    "tags": ["股东", "增减持", "筹码", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_holder_net_buy_ratio_90d": 0.35,
        "ext_alt_hs300_holder_top10_share_chg_qoq": 0.25,
        "uf_quality_value_defensive": 0.20,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_holder_net_buy_ratio_90d",
    "ext_alt_hs300_holder_top10_share_chg_qoq",
}
RULES = """
1. 只使用公告日已经可见的重要股东净增持和前十大流通股东变化。
2. 评分 = 重要股东净增持 35% + 股东集中度环比变化 25% + 质量价值 20% + 现金质量 20%。
3. 只保留90日内重要股东净增持为正的股票；持有35个交易日强制换出。
4. 实验策略；R62 的分组单调性未过，不能把 IC 正号直接当作可部署 alpha。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    net_buy = pl.col("ext_alt_hs300_holder_net_buy_ratio_90d")
    return pl.col("ma60").is_not_null() & net_buy.is_not_null() & (net_buy > 0)
