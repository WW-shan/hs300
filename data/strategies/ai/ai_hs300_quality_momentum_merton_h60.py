"""HS300 quality-momentum sleeve with an industry-neutral Merton DD proxy."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_merton_h60",
    "name": "HS300质量动量Merton违约距离(50仓/60日)",
    "description": "以行业中性Merton结构化违约距离代理替代C袖低波动腿的沪深300质量动量研究策略；50仓、60日持有（R351实验）",
    "tags": ["质量", "动量", "Merton", "违约距离", "低杠杆", "低波动", "行业中性", "沪深300", "实验", "R351"],
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
        "uf_merton_dd": 0.15,
    },
    "neutralize": {
        "industry": "industry_l1",
        "styles": [],
        "exclude": ["momentum_60d", "uf_quality_value_defensive", "uf_cash_quality"],
        "shrink": 1.0,
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
    "uf_merton_dd",
    "industry_l1",
}
RULES = """
1. 只允许60日动量为正的沪深300成分进入候选。
2. 评分 = 60日动量40% + 质量价值25% + 现金质量20% + Merton违约距离代理15%。
3. Merton代理按权益/负债比、20日已实现波动和零漂移一年期结构式计算；只在行业内去均值并 z-score，其他三条评分腿保持原值。
4. 因子只使用 TTM 财报 PIT 估值/杠杆和截至当日收盘的20日已实现波动，不使用未来数据。
5. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
6. R351研究策略；预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
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
    )
