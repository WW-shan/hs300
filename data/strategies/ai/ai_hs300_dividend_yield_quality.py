"""HS300 分红率/首次分红/分红增长 + 财务质量。"""
import polars as pl

META = {
    "id": "ai_hs300_dividend_yield_quality",
    "name": "HS300分红收益率质量",
    "description": "TTM分红收益率 + 首次分红 + 分红增长 + 质量价值/现金质量（实验）",
    "tags": ["分红", "股息", "首次分红", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_dividend_yield": 0.35,
        "ext_alt_hs300_dividend_initiation_flag": 0.15,
        "ext_alt_hs300_dividend_growth_ttm": 0.15,
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
    "ext_alt_hs300_dividend_yield",
    "ext_alt_hs300_dividend_initiation_flag",
    "ext_alt_hs300_dividend_growth_ttm",
}
RULES = """
1. TTM分红按公告日之后可见，收益率使用不复权收盘价。
2. 评分 = 分红收益率 35% + 首次分红 15% + 分红增长 15% + 质量价值 20% + 现金质量 15%。
3. 持有35个交易日强制换出；不加固定止损。
4. 实验策略；首次分红正样本稀疏，分组稳定性需要单独看。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return pl.col("ma60").is_not_null() & pl.col("ext_alt_hs300_dividend_yield").is_not_null()
