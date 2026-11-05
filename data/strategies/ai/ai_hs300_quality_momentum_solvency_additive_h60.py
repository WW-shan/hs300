"""HS300 quality-momentum sleeve with an incremental industry-neutral solvency leg."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_solvency_additive_h60",
    "name": "HS300质量动量+偿债缓冲增量(50仓/60日)",
    "description": "保留C袖原四腿相对权重，新增15%行业中性账面偿债缓冲的沪深300质量动量研究策略；50仓、60日持有（R348实验）",
    "tags": ["质量", "动量", "偿债缓冲", "增量因子", "低杠杆", "低波动", "行业中性", "沪深300", "实验", "R348"],
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
        "momentum_60d": 0.34,
        "uf_quality_value_defensive": 0.2125,
        "uf_cash_quality": 0.17,
        "uf_low_vol": 0.1275,
        "uf_solvency_buffer": 0.15,
    },
    "neutralize": {
        "industry": "industry_l1",
        "styles": [],
        "exclude": [
            "momentum_60d",
            "uf_quality_value_defensive",
            "uf_cash_quality",
            "uf_low_vol",
        ],
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
    "uf_low_vol",
    "uf_solvency_buffer",
    "industry_l1",
}
RULES = """
1. 只允许60日动量为正的沪深300成分进入候选。
2. 原C袖四腿乘0.85保留相对权重，新增15%账面偿债缓冲，总分仍为1。
3. 只有新增偿债缓冲腿做行业内去均值与z-score；原四腿不做行业中性。
4. 因子只使用 TTM 财报 PIT 负债率和截至当日收盘的20日年化波动，不使用未来数据。
5. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
6. R348研究策略；在R347替换实验失败后预注册，不得把结果当作独立发现。
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
