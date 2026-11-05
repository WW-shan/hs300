"""HS300 broad quality-momentum sleeve for R340 cost diversification research."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_broad_h60",
    "name": "HS300广义质量动量(50仓/60日)",
    "description": "60日动量为正且质量、现金质量、低波动完整的沪深300广义质量动量袖；50仓、60日持有（R340成本分散实验）",
    "tags": ["质量", "动量", "低波动", "低换手", "宽持仓", "沪深300", "实验", "R340"],
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
}
RULES = """
1. 只允许60日动量为正的沪深300成分进入候选。
2. 评分 = 60日动量40% + 质量价值25% + 现金质量20% + 低波动15%。
3. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
4. 宽持仓与较长持有期用于降低换手和单票利润集中度；不得据此调整权重或仓位网格。
5. R340研究策略，未通过预注册门槛前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("momentum_60d") > 0)
        & pl.col("roe_latest").is_not_null()
        & pl.col("pb_latest").is_not_null()
        & pl.col("debt_ratio_latest").is_not_null()
        & pl.col("ocf_ni").is_not_null()
        & pl.col("accrual").is_not_null()
        & pl.col("annual_vol_20d").is_not_null()
    )
