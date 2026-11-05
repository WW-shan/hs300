"""HS300 quality-momentum sleeve with a cross-factor agreement meta-factor."""
import polars as pl

META = {
    "id": "ai_hs300_quality_momentum_agreement_additive_h60",
    "name": "HS300质量动量一致数增补(50仓/60日)",
    "description": "在C袖四腿评分上增加15%行业中性四因子一致数，检验非线性因子一致性是否能扩大净利润来源；50仓、60日持有（R353实验）",
    "tags": ["质量", "动量", "现金质量", "低波", "因子一致", "组合", "行业中性", "沪深300", "实验", "R353"],
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
        "uf_factor_agreement": 0.15,
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
    "uf_factor_agreement",
    "industry_l1",
}
RULES = """
1. 只允许60日动量为正的沪深300成分进入候选。
2. 原C袖四腿权重按85%缩放：动量34%、质量价值21.25%、现金质量17%、低波12.75%；新增15%四因子一致数。
3. 一致数 = 动量、质量价值、现金质量、低波四项按同日截面排名处于前40%的项数（0-4），只在行业内去均值并 z-score；原四腿保持原值。
4. 所有排名只使用当日及以前的数据，不使用未来收益；财务字段使用PIT公告日。
5. 50个等权仓位，60个交易日强制换出，无固定止损，T+1开盘成交。
6. R353研究策略；预注册门槛全部通过前不得部署。
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
