"""HS300 质量相对强度匹配对照（R261）。"""

import polars as pl

META = {
    "id": "ai_hs300_qrs_low_lottery_control",
    "name": "HS300质量相对强度低彩票匹配对照",
    "description": (
        "与R261候选使用相同非金融股票池、ma60和新复合因子可用性过滤；"
        "评分保持R257原口径，但不包含低彩票复合因子。"
    ),
    "tags": [
        "质量",
        "行业相对强度",
        "低彩票需求",
        "匹配对照",
        "沪深300",
        "非金融",
        "实验",
        "R261",
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
        "ext_alt_hs300_qrs_quality_relative_strength_min_rank": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
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
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
    "ext_alt_hs300_qlot_quality_low_lottery_min_rank",
}
RULES = """
1. 与R261候选使用相同的历史沪深300非金融股票池、ma60和新复合因子可用性过滤；行业为空时 fail-closed。
2. 评分 = R257质量相对强度45% + 质量价值防御30% + 现金质量25%，不包含低彩票复合因子。
3. 30只评分池、最大10仓、等权、20日持有、-20%固定止损；用于隔离R261复合因子的增量贡献。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_qlot_quality_low_lottery_min_rank").is_not_null()
    )
