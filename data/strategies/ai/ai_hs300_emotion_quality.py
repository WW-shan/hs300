"""HS300 市场情绪分数闸门 + 财务质量。"""
import polars as pl

META = {
    "id": "ai_hs300_emotion_quality",
    "name": "HS300情绪质量",
    "description": "市场情绪分数高于阈值时持有质量价值+现金质量+低波动（实验）",
    "tags": ["情绪", "周期", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [{
        "id": "emotion_min", "label": "最低市场情绪分数", "type": "float",
        "default": 40.0, "min": 0.0, "max": 100.0, "step": 5.0,
    }],
    "scoring": {
        "uf_quality_value_defensive": 0.40,
        "uf_cash_quality": 0.35,
        "uf_low_vol": 0.25,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {"ma60", "ext_alt_hs300_market_emotion_score"}
RULES = """
1. 市场情绪分数低于阈值时禁止开新仓，已有持仓按35日规则离场。
2. 评分 = 质量价值 40% + 现金质量 35% + 低波动 25%。
3. 情绪分数来自 regime_history，按交易日向后对齐。
4. 实验策略；R24/R25 的情绪闸门历史结论不稳定。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    threshold = float(params.get("emotion_min", 40.0))
    emotion = pl.col("ext_alt_hs300_market_emotion_score")
    return pl.col("ma60").is_not_null() & emotion.is_not_null() & (emotion >= threshold)
