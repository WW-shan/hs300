"""HS300 industry-relative-strength quality sleeve with a core-earnings gate."""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_core_earnings_gate",
    "name": "HS300行业强弱质量核心利润门槛(10仓/20日)",
    "description": "B袖原行业相对强弱质量评分、20日持有和-20%止损不变，只增加TTM核心利润占比>=0.5的候选门槛（R358实验）",
    "tags": ["行业相对强弱", "行业动量", "质量", "核心利润", "财报", "沪深300", "实验", "R358"],
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
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
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
    "core_earnings_ratio",
    "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d",
    "uf_quality_value_defensive",
    "uf_cash_quality",
}
RULES = """
1. 原B袖评分保持：个股行业相对强弱35% + 行业动量15% + 质量价值30% + 现金质量20%。
2. 原B袖20日持有与-20%固定止损保持不变。
3. 仅新增固定候选门槛：TTM营业利润/TTM利润总额 >= 0.5；利润总额<=0或TTM不完整时不通过。
4. 核心利润占比使用公告日次日及以后的数据；0.5阈值不做网格。
5. R358研究策略；未通过预注册门槛前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    core = pl.col("core_earnings_ratio")
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & core.is_not_null()
        & (core >= 0.5)
    )
