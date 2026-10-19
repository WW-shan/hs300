"""HS300 价格延迟 + 质量价值 + 现金质量复合策略（R209 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_price_delay_quality_h20_sl20",
    "name": "HS300价格延迟质量",
    "description": "HS300市场信息价格延迟、质量价值防守与现金质量复合；20日持有、-20%止损（R209实验）",
    "tags": ["价格延迟", "信息扩散", "质量", "现金质量", "沪深300", "实验", "R209"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_price_delay_60d": 0.45,
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
    "ext_alt_hs300_price_delay_60d",
}
RULES = """
1. 价格延迟因子 = 个股对 HS300 一日滞后收益的滚动 beta / (同期 beta 绝对值 + 滞后 beta 绝对值)。
2. 评分 = 价格延迟 45% + 质量价值防守 30% + 现金质量 25%。
3. 30 只、20 日持有、-20% 固定止损；信号日使用截至当日收盘的数据，T+1 开盘执行。
4. R209 研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_price_delay_60d").is_not_null()
    )
