"""HS300 R257 软新鲜度惩罚策略（R260 研究候选）。"""

import polars as pl

META = {
    "id": "ai_hs300_qrs_soft_fresh_h20_sl20",
    "name": "HS300质量相对强度软新鲜度",
    "description": "以R257同日分位为主信号，对过去20日前30名出现次数施加最多20%的软惩罚；30只、10仓、20日持有、-20%止损（R260实验）。",
    "tags": [
        "质量",
        "行业相对强度",
        "软新鲜度",
        "集中度",
        "沪深300",
        "非金融",
        "实验",
        "R260",
    ],
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
        "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "industry_l1",
    "ext_alt_hs300_qrs_quality_relative_strength_min_rank",
    "ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. R257同日分位保持主信号；过去20个交易日进入R257严格Top30的次数只施加最多20%乘性惩罚。
3. 不排除任何股票，软Top30仍需满足每日候选池容量。
4. 评分 = 软新鲜度因子45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R260研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_qrsrf_quality_rs_soft_fresh_rank").is_not_null()
    )
