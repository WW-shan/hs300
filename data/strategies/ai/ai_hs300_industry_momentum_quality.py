"""HS300 行业动量 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_momentum_quality",
    "name": "HS300行业动量质量",
    "description": "行业20/60日动量、行业RPS、行业宽度 + 质量 + 低波动（实验）",
    "tags": ["行业动量", "RPS", "行业宽度", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_industry_mom_20d": 0.30,
        "ext_alt_hs300_industry_rps_20d": 0.20,
        "ext_alt_hs300_industry_breadth_20d": 0.15,
        "uf_quality_value_defensive": 0.20,
        "uf_low_vol": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_industry_mom_20d",
    "ext_alt_hs300_industry_rps_20d", "ext_alt_hs300_industry_breadth_20d",
}
RULES = """
1. 行业分类使用当前同花顺行业快照回填历史，存在行业回填偏差。
2. 行业动量/宽度只使用 T 日及以前收盘数据，T+1 开盘成交。
3. 评分 = 行业20日动量 30% + 行业RPS 20% + 行业宽度 15% + 质量 20% + 低波动 15%。
4. 持有 35 个交易日换出；R26 已记录行业动量单独不达门槛，本策略检验组合层。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_industry_mom_20d").is_not_null()
    )
