"""HS300 R97 跨维度另类因子组合。"""
import polars as pl

META = {
    "id": "ai_hs300_r97_combo",
    "name": "HS300另类因子组合R97",
    "description": "股东净增持/集中度变化/两融/商誉/股权供给/分红收益率/质量价值跨维度组合（实验）",
    "tags": ["组合", "股东", "两融", "商誉", "分红", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_holder_net_buy_ratio_90d": 0.20,
        "ext_alt_hs300_holder_top10_share_chg_qoq": 0.15,
        "ext_alt_hs300_margin_chg_1m_low": 0.15,
        "ext_alt_hs300_goodwill_ratio_low": 0.15,
        "ext_alt_hs300_equity_supply_ratio_180d_low": 0.10,
        "ext_alt_hs300_dividend_yield": 0.10,
        "uf_quality_value_defensive": 0.10,
        "uf_low_vol": 0.05,
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
    "ext_alt_hs300_holder_net_buy_ratio_90d",
    "ext_alt_hs300_holder_top10_share_chg_qoq",
    "ext_alt_hs300_margin_chg_1m_low",
    "ext_alt_hs300_goodwill_ratio_low",
    "ext_alt_hs300_equity_supply_ratio_180d_low",
    "ext_alt_hs300_dividend_yield",
}
RULES = """
1. 所有扩展因子按公告日/快照日之后可用，缺失值不填零。
2. 评分 = 净增持20% + 股东集中度变化15% + 两融反向15% + 商誉反向15% + 股权供给反向10% + 分红收益率10% + 质量价值10% + 低波动5%。
3. 持有35个交易日强制换出；不加固定止损。
4. 实验策略；组合结果必须和各单因子结果一起解读，不能只看组合收益。
"""

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    net_buy = pl.col("ext_alt_hs300_holder_net_buy_ratio_90d")
    holder_chg = pl.col("ext_alt_hs300_holder_top10_share_chg_qoq")
    margin = pl.col("ext_alt_hs300_margin_chg_1m_low")
    return pl.col("ma60").is_not_null() & (
        (net_buy > 0) | (holder_chg > 0) | margin.is_not_null()
    )
