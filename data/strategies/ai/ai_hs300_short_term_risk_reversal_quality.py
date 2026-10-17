"""HS300 短期风险反转复合 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_short_term_risk_reversal_quality",
    "name": "HS300短期风险反转质量",
    "description": "RSI/KDJ/EMA5/ATR/MACD DEA反向复合 + 财务质量（实验）",
    "tags": ["反转", "超买超卖", "ATR", "KDJ", "RSI", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "cf_short_term_risk_reversal": 0.55,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {"ma60"}
RULES = """
1. 短期风险反转复合对 RSI、KDJ D、EMA5乖离、ATR和MACD DEA取反向。
2. 评分 = 短期风险反转55% + 质量价值25% + 现金质量20%。
3. 20日持有、-20%止损；反转族历史不稳，以真实回测为准。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null()
