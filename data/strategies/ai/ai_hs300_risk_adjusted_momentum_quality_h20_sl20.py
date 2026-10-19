"""HS300 风险调整动量 + 质量价值 + 现金质量复合策略（R214 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_risk_adjusted_momentum_quality_h20_sl20",
    "name": "HS300风险调整动量质量",
    "description": "60日风险调整动量、质量价值防守与现金质量复合；20日持有、-20%止损（R214实验）",
    "tags": ["风险调整动量", "动量", "波动率", "质量", "现金质量", "沪深300", "实验", "R214"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_risk_adjusted_momentum_60d": 0.45,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.25,
    },
    "order_by": "score", "descending": True, "limit": 30,
    "research_only": True,
}
EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20
REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_risk_adjusted_momentum_60d",
}
RULES = """
1. 风险调整动量 = 60日净收益 / 同期年化已实现波动率。
2. 波动率非正时因子为空；信号日只使用截至当日收盘的数据，T+1开盘执行。
3. 评分 = 风险调整动量 45% + 质量价值防守 30% + 现金质量 25%。
4. 30只、20日持有、-20%固定止损。
5. R214研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_risk_adjusted_momentum_60d").is_not_null()
    )