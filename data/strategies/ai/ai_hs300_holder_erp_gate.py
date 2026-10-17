"""HS300 股东集中度 + 质量价值 + ERP 市场闸门。

ERP = 沪深300盈利收益率 - 10年国债收益率。R93 只把它作为接近候选的
市场级软闸门；本策略用参数化阈值做真实回测，不把单次最优阈值当作结论。
"""
import polars as pl

META = {
    "id": "ai_hs300_holder_erp_gate",
    "name": "HS300筹码集中ERP闸门",
    "description": "股东集中度+质量价值+低波动，仅在ERP高于阈值时开新仓（实验）",
    "tags": ["筹码", "ERP", "择时", "质量", "沪深300", "实验"],
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
    "params": [
        {
            "id": "erp_min",
            "label": "ERP最低阈值(百分点)",
            "type": "float",
            "default": 5.5,
            "min": 3.0,
            "max": 9.0,
            "step": 0.1,
        },
    ],
    "scoring": {
        "ext_alt_hs300_holder_top10_free_float_share": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_low_vol": 0.25,
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
    "ext_alt_hs300_holder_top10_free_float_share",
    "ext_alt_hs300_equity_risk_premium",
}

RULES = """
1. 候选池为当前沪深300成分，只使用公告日之后可见的股东集中度。
2. ERP 是市场级状态变量，当前日 ERP 低于阈值时禁止开新仓；已有持仓仍按规则离场。
3. 评分 = 股东集中度 45% + 质量价值防守 30% + 低波动 25%。
4. 持有满 35 个交易日强制换出；ERP 阈值作为参数做敏感性回测。
5. 实验策略；ERP 的显著性未过研究门槛，不能据此宣称择时有效。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    threshold = float(params.get("erp_min", 5.5))
    holder = pl.col("ext_alt_hs300_holder_top10_free_float_share")
    erp = pl.col("ext_alt_hs300_equity_risk_premium")
    return (
        pl.col("ma60").is_not_null()
        & holder.is_not_null()
        & erp.is_not_null()
        & (erp >= threshold)
    )
