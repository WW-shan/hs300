"""HS300 市场已实现偏度闸门 + 质量价值 + 现金质量策略（R215 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_market_skew_quality_gate_h20_sl20",
    "name": "HS300市场偏度质量闸门",
    "description": "仅在HS300 60日已实现偏度为正时，按质量价值与现金质量选股；20日持有、-20%止损（R215实验）",
    "tags": ["市场偏度", "已实现偏度", "择时", "质量", "现金质量", "沪深300", "实验", "R215"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "uf_quality_value_defensive": 0.545,
        "uf_cash_quality": 0.455,
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
    "ext_alt_hs300_market_skew_60d_low",
}
RULES = """
1. 市场偏度因子为 HS300 60日简单收益总体偏度的负值，高值代表上行尾部占优。
2. 仅在 `ext_alt_hs300_market_skew_60d_low > 0` 时允许开仓。
3. 评分 = 质量价值防守 54.5% + 现金质量 45.5%。
4. 30只、20日持有、-20%固定止损；信号日使用截至当日收盘的数据，T+1开盘执行。
5. R215研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_market_skew_60d_low") > 0.0)
    )