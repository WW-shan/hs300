"""HS300 质量价值 + 现金质量匹配对照（R218）。"""
import polars as pl

META = {
    "id": "ai_hs300_net_financing_quality_control",
    "name": "HS300净筹资强度匹配对照",
    "description": "与R218候选使用相同股票池、净筹资因子可用性过滤和执行口径，但不把净筹资强度计入评分",
    "tags": ["质量", "现金质量", "匹配对照", "沪深300", "实验", "R218"],
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
    "ext_alt_hs300_net_financing_4q_low",
}
RULES = """
1. 与R218候选使用相同的 net_financing_4q_low 可用性过滤，确保股票池一致。
2. 评分 = 质量价值防守54.5% + 现金质量45.5%，不包含净筹资强度排序。
3. 30只、20日持有、-20%固定止损；用于隔离净筹资强度的排序贡献。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_net_financing_4q_low").is_not_null()
    )