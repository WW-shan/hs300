"""HS300 多事件/行为维度组合实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_event_combo",
    "name": "HS300事件行为组合",
    "description": "龙虎榜+回购+分析师+业绩预期差+质量+现金质量（实验）",
    "tags": ["事件", "龙虎榜", "回购", "分析师", "PEAD", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_lhb_net_buy_ratio_20d": 0.15,
        "ext_alt_hs300_repurchase_event_60d": 0.15,
        "ext_alt_hs300_analyst_rating_score_20d": 0.20,
        "ext_alt_hs300_forecast_surprise_60d": 0.15,
        "ext_alt_hs300_express_surprise_60d": 0.10,
        "uf_quality_value_defensive": 0.15,
        "uf_cash_quality": 0.10,
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
    "ext_alt_hs300_lhb_net_buy_ratio_20d",
    "ext_alt_hs300_repurchase_event_60d",
    "ext_alt_hs300_analyst_rating_score_20d",
    "ext_alt_hs300_forecast_surprise_60d",
    "ext_alt_hs300_express_surprise_60d",
}
RULES = """
1. 所有事件均在公告后下一交易日生效，并按各自窗口衰减。
2. 至少有一个正向事件、回购、业绩预期差或分析师覆盖才进入候选。
3. 评分 = 龙虎榜 15% + 回购 15% + 分析师 20% + 预告 15% + 快报 10%
   + 质量 15% + 现金质量 10%。
4. 持有 20 个交易日换出；多事件组合只做实验，不把事件数量当成质量。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (
            (pl.col("ext_alt_hs300_lhb_net_buy_ratio_20d") > 0)
            | (pl.col("ext_alt_hs300_repurchase_event_60d") > 0)
            | (pl.col("ext_alt_hs300_forecast_surprise_60d") > 0)
            | (pl.col("ext_alt_hs300_express_surprise_60d") > 0)
            | pl.col("ext_alt_hs300_analyst_rating_score_20d").is_not_null()
        )
    )
