"""HS300 ETF份额/QVIX 风险偏好闸门 + 财务质量实验策略。"""
import polars as pl

META = {
    "id": "ai_hs300_risk_appetite_quality",
    "name": "HS300风险偏好质量",
    "description": "ETF份额扩张且QVIX不过热时持有质量/低波动组合（实验）",
    "tags": ["ETF", "份额", "QVIX", "风险偏好", "质量", "沪深300", "实验"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_etf_share_chg_1m": 0.20,
        "ext_alt_hs300_industry_mom_20d": 0.20,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    },
    "order_by": "score", "descending": True, "limit": 30,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 35
REQUIRED_FEATURES = {
    "ma60", "ext_alt_hs300_etf_share_chg_1m", "ext_alt_hs300_qvix_z_252",
    "ext_alt_hs300_industry_mom_20d",
}
RULES = """
1. ETF份额月度快照后下一日生效，超过45天不使用；QVIX按252日z-score。
2. 只在 ETF 份额环比扩张且 QVIX z-score < 1.5 时允许开新仓。
3. 评分 = ETF份额 20% + 行业动量 20% + 质量 25% + 现金质量 20% + 低波动 15%。
4. 持有 35 个交易日换出；R74/R75 单独研究均为负知识，本策略只检验组合闸门。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & (pl.col("ext_alt_hs300_etf_share_chg_1m") > 0)
        & (pl.col("ext_alt_hs300_qvix_z_252") < 1.5)
    )
