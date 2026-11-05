"""HS300 quality-momentum sleeve with an industry-neutral solvency buffer."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_solvency_h60",
    "name": "HS300质量动量偿债缓冲(50仓/60日)",
    "description": "以行业中性账面偿债缓冲替代C袖低波动腿的沪深300质量动量研究策略；50仓、60日持有（R347实验）",
    "tags": ["质量", "动量", "偿债缓冲", "低杠杆", "低波动", "行业中性", "沪深300", "实验", "R347"],
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
        "uf_solvency_buffer": 0.15,
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
    "uf_solvency_buffer",
    "industry_l1",
}
RULES = """
1. 只允许60日动量为正的沪深300成分进入候选。
2. 评分 = 60日动量40% + 质量价值25% + 现金质量20% + 账面偿债缓冲15%。
3. 账面偿债缓冲仅在行业内去均值并 z-score；其他三条评分腿保持原值，不做行业中性。
4. 因子只使用 TTM 财报 PIT 负债率和截至当日收盘的20日年化波动，不使用未来数据。
5. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
6. R347研究策略；预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("momentum_60d") > 0)
        & pl.col("roe_latest").is_not_null()
        & pl.col("pb_latest").is_not_null()
        & pl.col("debt_ratio_latest").is_not_null()
        & (pl.col("debt_ratio_latest") >= 0.0)
        & (pl.col("debt_ratio_latest") < 100.0)
        & pl.col("ocf_ni").is_not_null()
        & pl.col("accrual").is_not_null()
        & (pl.col("annual_vol_20d") > 0.0)
        & pl.col("industry_l1").is_not_null()
    )
