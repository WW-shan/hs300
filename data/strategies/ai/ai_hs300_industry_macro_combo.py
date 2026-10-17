"""HS300 行业动量 + 宏观流动性 + 财务质量组合实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_macro_combo",
    "name": "HS300行业宏观组合",
    "description": "行业RPS/相对强弱 + M1-M2改善 + 质量/现金/低波动（实验）",
    "tags": ["行业", "宏观", "流动性", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_industry_rps_20d": 0.25,
        "ext_alt_hs300_stock_rel_industry_20d": 0.15,
        "ext_alt_hs300_m1_m2_scissors_chg_1m": 0.10,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.15,
        "uf_low_vol": 0.10,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_industry_rps_20d",
    "ext_alt_hs300_stock_rel_industry_20d", "ext_alt_hs300_m1_m2_scissors_chg_1m",
}
RULES = """
1. 行业分类使用当前快照回填，存在行业回填偏差。
2. 只有 M1-M2 剪刀差环比改善时允许开新仓。
3. 评分 = 行业RPS 25% + 行业内相对强弱 15% + 流动性改善 10%
   + 质量 25% + 现金质量 15% + 低波动 10%。
4. 持有 35 个交易日换出；组合腿更多不等于更稳，必须看相位和近一年结果。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_m1_m2_scissors_chg_1m") > 0)
        & pl.col("ext_alt_hs300_industry_rps_20d").is_not_null()
    )
