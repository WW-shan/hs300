"""HS300 质量 × 行业相对强度最弱一环策略（R257 研究候选）。"""

import polars as pl

META = {
    "id": "ai_hs300_qrs_h10_sl20",
    "name": "HS300质量相对强度持有10日",
    "description": "R257质量相对强度 + 10日强制换出；30只、10仓、-20%止损（R262实验）。",
    "tags": ["质量", "行业相对强度", "最弱一环", "沪深300", "非金融", "实验", "R257", "R262", "h10"],
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
MAX_HOLD_DAYS = 10
REQUIRED_FEATURES = {
    "ma60",
    "industry_l1",
    "ext_alt_hs300_minrank_management_ocf_gm_min_rank",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. R257因子对R253质量最弱一环与20日行业相对强度分别做同日横截面分位，再取两者最小值。
3. 评分 = 质量相对强度45% + 质量价值防守30% + 现金质量25%。
4. 30只评分池、最多10仓、等权、10日持有、-20%固定止损；T+1开盘执行。
5. R262组合层实验变体（h10），预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_qrs_quality_relative_strength_min_rank").is_not_null()
    )
