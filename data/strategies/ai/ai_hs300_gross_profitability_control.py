"""HS300 非金融质量价值 + 现金质量匹配对照（R244）。"""
import polars as pl

META = {
    "id": "ai_hs300_gross_profitability_control",
    "name": "HS300非金融毛利生产率匹配对照",
    "description": "与R244候选使用相同非金融股票池、毛利生产率因子可用性过滤和执行口径，但不把毛利生产率计入评分",
    "tags": ["质量", "现金质量", "匹配对照", "沪深300", "非金融", "实验", "R244"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "uf_quality_value_defensive": 0.545,
        "uf_cash_quality": 0.455,
    },
    "order_by": "score", "descending": True, "limit": 30,
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
    "ext_alt_hs300_gp_gross_profitability",
}
RULES = """
1. 与R244候选使用相同的历史沪深300非金融股票池；行业为空时 fail-closed。
2. 与候选使用相同的毛利生产率因子可用性过滤，确保股票池一致。
3. 评分 = 质量价值防守54.5% + 现金质量45.5%，不包含毛利生产率排序。
4. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；用于隔离毛利生产率的排序贡献。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_gp_gross_profitability").is_not_null()
    )