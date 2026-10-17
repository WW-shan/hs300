"""HS300 业绩预告/快报预期差 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_earnings_surprise_quality",
    "name": "HS300业绩预期差质量",
    "description": "业绩预告/快报正向预期差 + 质量价值 + 现金质量（实验）",
    "tags": ["业绩预告", "业绩快报", "PEAD", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_forecast_surprise_60d": 0.35,
        "ext_alt_hs300_express_surprise_60d": 0.25,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_forecast_surprise_60d",
    "ext_alt_hs300_express_surprise_60d",
}
RULES = """
1. 业绩预告/快报公告后下一交易日生效，60 个交易日内线性衰减。
2. 只保留正向预告或快报预期差的股票。
3. 评分 = 预告预期差 35% + 快报预期差 25% + 质量 25% + 现金质量 15%。
4. 持有 20 个交易日换出；PEAD 维度此前未过完整研究门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (
            (pl.col("ext_alt_hs300_forecast_surprise_60d") > 0)
            | (pl.col("ext_alt_hs300_express_surprise_60d") > 0)
        )
    )
