"""HS300 200 日趋势入场闸门匹配对照（R255）。"""

import polars as pl

META = {
    "id": "ai_hs300_minrank_ma200_gate_quality_control",
    "name": "HS300 200日趋势闸门匹配对照",
    "description": "与R255候选使用相同200日趋势闸门、非金融股票池和R253因子可用性过滤，但不把质量最弱一环计入评分",
    "tags": [
        "200日均线",
        "趋势闸门",
        "质量",
        "现金质量",
        "匹配对照",
        "沪深300",
        "非金融",
        "实验",
        "R255",
    ],
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
        "uf_quality_value_defensive": 0.545,
        "uf_cash_quality": 0.455,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "industry_l1",
    "ext_alt_hs300_minrank_management_ocf_gm_min_rank",
    "ext_alt_market_regime_hs300_above_ma200",
}
RULES = """
1. 与R255候选使用相同的历史沪深300非金融股票池、200日趋势新开仓闸门和R253因子可用性过滤；行业为空时 fail-closed。
2. 闸门仅限制新开仓，不强制卖出已有仓位。
3. 评分 = 质量价值防守54.5% + 现金质量45.5%，不包含R253质量因子。
4. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；用于隔离质量因子贡献。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_minrank_management_ocf_gm_min_rank").is_not_null()
        & (pl.col("ext_alt_market_regime_hs300_above_ma200") == 1)
    )
