"""HS300 行业相对强弱质量 — 20日/止损20%/宽度闸门防守版。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_relative_strength_quality_h20_sl20_breadth45",
    "name": "HS300行业内相对强弱质量(20日/止损20%/宽度45)",
    "description": "最终R102候选叠加市场站上MA20比例≥45%的开仓闸门，降低回撤（实验）",
    "tags": ["行业中性", "相对强弱", "质量", "止损", "市场宽度", "择时", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [
        {"id": "breadth_min", "label": "站上MA20比例下限", "type": "float", "default": 0.45, "min": 0.20, "max": 0.80, "step": 0.05},
    ],
    "scoring": {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_stock_rel_industry_20d",
    "ext_alt_hs300_industry_mom_20d", "ext_alt_hs300_market_breadth_above_ma20",
}
RULES = """
1. 评分与 R102 最终候选相同；持有期20日、固定-20%止损。
2. 只有市场站上MA20比例 ≥45% 时才允许开新仓；已有持仓不因闸门变化强制卖出。
3. 宽度闸门主要降低回撤，不保证提高收益；近一年正收益相位数可能下降。
4. 市场宽度数据来自 regime_history，只使用 T 日及以前值。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    breadth_min = float(params.get("breadth_min", 0.45))
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_stock_rel_industry_20d").is_not_null()
        & (pl.col("ext_alt_hs300_market_breadth_above_ma20") >= breadth_min)
    )
