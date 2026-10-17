"""HS300 行业内相对强弱质量 — 20日持有期参数优化实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20",
    "name": "HS300行业内相对强弱质量(持有20日)",
    "description": "R101行业相对强弱质量评分不变，持有期由35日优化为20日（实验）",
    "tags": ["行业中性", "相对强弱", "质量", "参数优化", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d",
}
RULES = """
1. 评分与 R101 行业内相对强弱质量策略完全相同。
2. 仅把持有期从 35 日改为 20 日；参数网格显示 20/35 日在 10 持仓下形成收益平台。
3. 15/20 持仓数单调变差，不采纳；max_positions 保持 10。
4. 行业分类为当前快照回填，存在行业回填偏差。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
