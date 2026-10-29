"""R272 matched quality/cash control with the same factor availability filter."""
import polars as pl

META = {
    "id": "ai_hs300_operating_margin_improvement_quality_control",
    "name": "HS300 operating margin R272 matched control",
    "description": "Matched quality/cash control for R272; uses the same historical non-financial HS300 eligibility filter without scoring the tested factor.",
    "tags": ["R272", "matched control", "research"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"price_min": 3, "price_max": 500, "market_cap_min": 10e8, "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60},
    "params": [],
    "scoring": {"uf_quality_value_defensive": 0.545, "uf_cash_quality": 0.455},
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
