"""HS300 macro-gated quality sleeve with a core-earnings candidate gate."""
import polars as pl

META = {
    "id": "ai_hs300_macro_gate_quality_core_earnings_gate",
    "name": "HS300宏观质量核心利润门槛(10仓/35日)",
    "description": "A袖原有宏观三闸门与评分不变，只增加TTM核心利润占比>=0.5的候选门槛；检验营业外利润依赖是否改善尾部与净利润宽度（R355实验）",
    "tags": ["宏观", "质量", "核心利润", "营业利润", "财报", "沪深300", "实验", "R355"],
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
    "params": [
        {"id": "erp_min", "label": "ERP最低阈值", "type": "float", "default": 5.5, "min": 3.0, "max": 9.0, "step": 0.1},
        {"id": "credit_max", "label": "信用利差最高阈值", "type": "float", "default": 0.8, "min": 0.2, "max": 1.5, "step": 0.05},
        {"id": "term_min", "label": "期限利差最低阈值", "type": "float", "default": 0.3, "min": 0.0, "max": 1.5, "step": 0.05},
    ],
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.35,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
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
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60",
    "core_earnings_ratio",
    "ext_alt_hs300_holder_top10_free_float_share",
    "uf_quality_value_defensive",
    "uf_low_vol",
    "uf_cash_quality",
    "ext_alt_hs300_equity_risk_premium",
    "ext_alt_hs300_credit_spread_5y_low",
    "ext_alt_hs300_term_spread_10y_2y",
}
RULES = """
1. 原A袖的ERP、信用利差、期限利差三闸门与35日持有规则完全不变。
2. 原A袖评分保持：股东集中度35% + 质量价值25% + 低波20% + 现金质量20%。
3. 仅新增固定候选门槛：TTM营业利润/TTM利润总额 >= 0.5；利润总额<=0或TTM不完整时不通过。
4. 核心利润占比使用公告日次日及以后的数据，不使用未来财报；0.5阈值表示利润至少一半来自主营经营，不做阈值网格。
5. R355研究策略；未通过预注册门槛前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    erp_min = float(params.get("erp_min", 5.5))
    credit_max = float(params.get("credit_max", 0.8))
    term_min = float(params.get("term_min", 0.3))
    core = pl.col("core_earnings_ratio")
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_holder_top10_free_float_share").is_not_null()
        & (pl.col("ext_alt_hs300_equity_risk_premium") >= erp_min)
        & (pl.col("ext_alt_hs300_credit_spread_5y_low") >= -credit_max)
        & (pl.col("ext_alt_hs300_term_spread_10y_2y") >= term_min)
        & core.is_not_null()
        & (core >= 0.5)
    )
