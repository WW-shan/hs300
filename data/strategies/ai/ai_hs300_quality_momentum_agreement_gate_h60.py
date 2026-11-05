"""HS300 quality-momentum C sleeve with a four-factor agreement gate."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_agreement_gate_h60",
    "name": "HS300质量动量一致数门槛(50仓/60日)",
    "description": "在C袖原40/25/20/15评分上，只保留四因子一致数至少为2的候选；50仓、60日持有（R354实验）",
    "tags": ["质量", "动量", "现金质量", "低波", "因子一致", "候选门槛", "沪深300", "实验", "R354"],
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
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    },
    "order_by": "score",
    "descending": True,
    "limit": 100,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 60
REQUIRED_FEATURES = {
    "ma60",
    "momentum_60d",
    "uf_quality_value_defensive",
    "uf_cash_quality",
    "uf_low_vol",
    "industry_l1",
}
RULES = """
1. 候选首先满足原C袖的动量、PIT财务字段、行业和流动性过滤。
2. 一致数 = 动量、质量价值、现金质量、低波四项在当日截面排名前40%的项数；只保留一致数 >= 2 的候选。
3. 评分仍为原C袖：60日动量40% + 质量价值25% + 现金质量20% + 低波15%。
4. 一致数只使用当天及以前已知的横截面数据；不使用未来收益，阈值2和排名前40%冻结，不做网格。
5. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
6. R354研究策略；预注册门槛全部通过前不得部署。
"""


def _top_rank(value: pl.Expr) -> pl.Expr:
    return value.rank(method="average").over("date") > 0.6 * pl.len().over("date")


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    quality = pl.col("roe_latest") / (100.0 * pl.col("pb_latest")) - pl.col("debt_ratio_latest") / 100.0
    cash = pl.col("ocf_ni") - pl.col("accrual")
    low_vol = 0.0 - pl.col("annual_vol_20d")
    agreement = (
        _top_rank(pl.col("momentum_60d")).cast(pl.Int64)
        + _top_rank(quality).cast(pl.Int64)
        + _top_rank(cash).cast(pl.Int64)
        + _top_rank(low_vol).cast(pl.Int64)
    )
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("momentum_60d") > 0)
        & pl.col("roe_latest").is_not_null()
        & pl.col("pb_latest").is_not_null()
        & (pl.col("pb_latest") > 0.0)
        & pl.col("debt_ratio_latest").is_not_null()
        & (pl.col("debt_ratio_latest") > 0.0)
        & (pl.col("debt_ratio_latest") < 100.0)
        & pl.col("ocf_ni").is_not_null()
        & pl.col("accrual").is_not_null()
        & (pl.col("annual_vol_20d") > 0.0)
        & pl.col("industry_l1").is_not_null()
        & (agreement >= 2)
    )
