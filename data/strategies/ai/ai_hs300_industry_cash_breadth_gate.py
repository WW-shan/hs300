"""HS300 行业低风险质量 + 市场宽度闸门实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_cash_breadth_gate",
    "name": "HS300行业低风险质量宽度闸门",
    "description": "行业相对强弱+低波+质量，在市场站上MA20比例达阈值时持有（实验）",
    "tags": ["行业", "相对强弱", "低波动", "市场宽度", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [
        {"id": "breadth_min", "label": "站上MA20比例下限", "type": "float", "default": 0.45, "min": 0.20, "max": 0.80, "step": 0.05},
    ],
    "scoring": {
        "cf_industry_lowrisk_quality": 0.70,
        "ext_alt_hs300_industry_rps_20d": 0.15,
        "uf_cash_quality": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_rps_20d", "ext_alt_hs300_market_breadth_above_ma20",
}
RULES = """
1. 市场站上 MA20 的股票比例 >= breadth_min 时才允许开新仓。
2. 评分 = 行业强弱低风险质量复合 70% + 行业RPS 15% + 现金质量 15%。
3. 已有持仓不因闸门变化立即卖出，持有满 35 个交易日换出。
4. 宽度阈值是预先固定的 0.45 中位水平，不在主回测后挑峰值。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    breadth_min = float(params.get("breadth_min", 0.45))
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & (pl.col("ext_alt_hs300_market_breadth_above_ma20") >= breadth_min)
    )
