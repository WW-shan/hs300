"""HS300 商誉/无形资产/研发与资本开支 + 财务质量。"""
import polars as pl

META = {
    "id": "ai_hs300_goodwill_quality",
    "name": "HS300商誉研发质量",
    "description": "低商誉、低无形资产、研发强度、低资本开支 + 质量价值/现金质量（实验）",
    "tags": ["商誉", "研发", "无形资产", "资本开支", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_goodwill_ratio_low": 0.35,
        "ext_alt_hs300_intangible_ratio_low": 0.15,
        "ext_alt_hs300_rd_intensity": 0.15,
        "uf_quality_value_defensive": 0.20,
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
    "ma60",
    "ext_alt_hs300_goodwill_ratio_low",
    "ext_alt_hs300_intangible_ratio_low",
    "ext_alt_hs300_rd_intensity",
}
RULES = """
1. 商誉/无形资产/研发/资本开支按公告日后下一交易日生效。
2. 评分 = 低商誉 35% + 低无形资产 15% + 研发强度 15% + 质量价值 20% + 现金质量 15%。
3. 持有35个交易日强制换出；不加固定止损。
4. 实验策略；R86 只有商誉因子在两段 IC 同号，其他腿是探索项。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_goodwill_ratio_low").is_not_null()
