"""HS300 另类因子跨维度组合。

把增持事件、股东集中度、分红增长、质量价值和现金质量放在同一评分中，
检验“多个弱信号组合”是否比单因子原型更稳定。ERP 不在此策略中硬编码，
另由 ai_hs300_holder_erp_gate 单独检验市场闸门。
"""
import polars as pl

META = {
    "id": "ai_hs300_alt_factor_combo",
    "name": "HS300另类因子组合",
    "description": "增持+股东集中度+分红增长+质量价值+现金质量跨维度组合（实验）",
    "tags": ["组合", "增持", "筹码", "分红", "质量", "价值", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.2e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_insider_buy_20d": 0.20,
        "ext_alt_hs300_holder_top10_free_float_share": 0.25,
        "ext_alt_hs300_dividend_growth_ttm": 0.25,
        "uf_quality_value_defensive": 0.15,
        "uf_cash_quality": 0.15,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35

REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_insider_buy_20d",
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_dividend_growth_ttm",
}

RULES = """
1. 候选池为当前沪深300成分，所有扩展因子按公告日之后的历史日期对齐。
2. 评分 = 增持事件 20% + 股东集中度 25% + 分红增长 25% + 质量价值 15% + 现金质量 15%。
3. 允许没有增持事件的股票靠其他腿进入，事件只作为加分项，不做硬过滤。
4. 持有满 35 个交易日强制换出；不加固定止损。
5. 实验策略；各因子的验证段衰减和相关性必须结合证据文件解读。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    holder = pl.col("ext_alt_hs300_holder_top10_free_float_share")
    dividend = pl.col("ext_alt_hs300_dividend_growth_ttm")
    return pl.col("ma60").is_not_null() & (holder.is_not_null() | dividend.is_not_null())
