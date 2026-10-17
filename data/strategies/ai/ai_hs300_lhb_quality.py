"""HS300 龙虎榜资金流 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_lhb_quality",
    "name": "HS300龙虎榜资金质量",
    "description": "龙虎榜净买/机构净买 + 低上榜拥挤 + 质量价值 + 低波动（实验）",
    "tags": ["龙虎榜", "资金流", "事件", "质量", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_lhb_net_buy_ratio_20d": 0.35,
        "ext_alt_hs300_lhb_inst_net_buy_ratio_20d": 0.25,
        "ext_alt_hs300_lhb_event_count_20d_low": 0.15,
        "uf_quality_value_defensive": 0.15,
        "uf_low_vol": 0.10,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_lhb_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_inst_net_buy_ratio_20d",
    "ext_alt_hs300_lhb_event_count_20d_low",
}
RULES = """
1. 龙虎榜公告后下一交易日生效，20 个交易日内线性衰减。
2. 只保留近期龙虎榜净买或机构净买为正的股票。
3. 评分 = 净买 35% + 机构净买 25% + 低上榜拥挤 15% + 质量 15% + 低波动 10%。
4. 持有 20 个交易日换出；实验策略，事件研究单独未过门槛。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (
            (pl.col("ext_alt_hs300_lhb_net_buy_ratio_20d") > 0)
            | (pl.col("ext_alt_hs300_lhb_inst_net_buy_ratio_20d") > 0)
        )
    )
