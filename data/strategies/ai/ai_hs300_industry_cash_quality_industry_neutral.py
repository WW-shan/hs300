"""HS300 行业强弱现金质量 — 行业中性收缩实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_cash_quality_industry_neutral",
    "name": "HS300行业强弱现金质量(行业中性0.5)",
    "description": "行业强弱现金质量复合评分做行业中性化，收缩系数0.5（实验）",
    "tags": ["行业中性", "相对强弱", "现金质量", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_industry_cash_quality": 0.65,
        "ext_alt_hs300_industry_rps_20d": 0.15,
        "uf_low_vol": 0.20,
    },
    "neutralize": {"industry": "industry_l1", "shrink": 0.5},
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_rps_20d",
}
RULES = """
1. 评分腿与原版相同。
2. 每只股票评分先按行业去均值/标准化，再以 0.5 收缩回原始评分，降低行业暴露但保留部分轮动收益。
3. 只使用 T 日及以前数据，T+1 开盘成交；持有 35 个交易日换出。
4. 行业分类为当前快照回填，存在回填偏差。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & pl.col("ext_alt_hs300_industry_rps_20d").is_not_null()
    )
