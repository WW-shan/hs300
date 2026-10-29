"""R272 research-only operating-margin improvement strategy."""
import polars as pl

META = {
    "id": "ai_hs300_operating_margin_improvement_quality_h20_sl20",
    "name": "HS300 operating leverage improvement (R272 research)",
    "description": "Ranks non-financial historical HS300 members by year-over-year improvement in single-quarter operating-profit margin, combined with quality and cash factors.",
    "tags": ["R272", "operating margin", "quality", "research"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8, "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60},
    "params": [],
    "scoring": {
        "ext_alt_hs300_opm_operating_margin_yoy_change_q": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
    },
    "order_by": "score", "descending": True, "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60", "industry_l1", "ext_alt_hs300_opm_operating_margin_yoy_change_q"}


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_opm_operating_margin_yoy_change_q").is_not_null()
    )
