"""HS300 Corwin-Schultz 价差 + 质量价值 + 现金质量复合策略（R210 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_cs_spread_quality_h20_sl20",
    "name": "HS300流动性价差质量",
    "description": "Corwin-Schultz高低价估计买卖价差、质量价值防守与现金质量复合；20日持有、-20%止损（R210实验）",
    "tags": ["流动性", "买卖价差", "Corwin-Schultz", "质量", "现金质量", "沪深300", "实验", "R210"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_cs_spread_60d": 0.45,
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
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_cs_spread_60d",
}
RULES = """
1. 价差因子 = 连续两日调整后高低价构造的 Corwin-Schultz 有效价差估计，再做 60 日均值。
2. 为防未来函数，t 日只使用 t-1/t 两日估计；t/t+1 估计不提前使用。
3. 评分 = 流动性价差 45% + 质量价值防守 30% + 现金质量 25%。
4. 30 只、20 日持有、-20% 固定止损；信号日使用截至当日收盘的数据，T+1 开盘执行。
5. R210 研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_cs_spread_60d").is_not_null()
    )
