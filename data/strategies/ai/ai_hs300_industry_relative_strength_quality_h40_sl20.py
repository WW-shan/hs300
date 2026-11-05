"""HS300 行业内相对强弱质量 — 40日持有 + 20%固定止损实验版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h40_sl20",
    "name": "HS300行业内相对强弱质量(40日/止损20%)",
    "description": "R101行业相对强弱质量评分，40日持有并用-20%固定止损控制回撤（R337低换手实验）",
    "tags": ["行业中性", "相对强弱", "质量", "止损", "低换手", "沪深300", "实验", "R337"],
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
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 40
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_industry_mom_20d",
}
RULES = """
1. 评分、宇宙和 -20% 止损与 h20 版本完全相同。
2. 只把最长持有期从 20 日延长到 40 日，以降低换手和成本敏感度。
3. 这是 R337 单一结构实验；不得据此改变评分、权重或止损。
4. 行业分类为当前快照回填，仍为实验策略。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
    )
