"""HS300 低特质波动 + 质量价值 + 现金质量复合策略（R208 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_idio_vol_quality_h20_sl20",
    "name": "HS300低特质波动质量",
    "description": "HS300市场模型残差波动率反向、质量价值防守与现金质量复合；20日持有、-20%止损（R208实验）",
    "tags": ["特质波动", "残差波动率", "低波", "质量", "现金质量", "沪深300", "实验", "R208"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_idio_vol_60d_low": 0.45,
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
    "ext_alt_hs300_idio_vol_60d_low",
}
RULES = """
1. 低特质波动因子 = 个股日收益对 HS300 日收益的 60 个收益观测滚动市场模型残差波动率取负。
2. 评分 = 低特质波动 45% + 质量价值防守 30% + 现金质量 25%。
3. 30 只、20 日持有、-20% 固定止损；信号日使用截至当日收盘的数据，T+1 开盘执行。
4. R208 研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_idio_vol_60d_low").is_not_null()
    )
