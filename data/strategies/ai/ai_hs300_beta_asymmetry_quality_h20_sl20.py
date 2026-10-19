"""HS300 条件 beta 不对称性 + 质量价值 + 现金质量复合策略（R217 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_beta_asymmetry_quality_h20_sl20",
    "name": "HS300 Beta不对称性质量",
    "description": "120日上行beta减下行beta、质量价值防守与现金质量复合；20日持有、-20%止损（R217实验）",
    "tags": ["Beta不对称性", "上行Beta", "下行Beta", "质量", "现金质量", "沪深300", "实验", "R217"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_beta_asymmetry_120d": 0.45,
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
    "ext_alt_hs300_beta_asymmetry_120d",
}
RULES = """
1. 在120个收益观测内，分别估计HS300上涨日与下跌日的个股条件beta。
2. 因子 = 上行beta - 下行beta；高值代表上涨参与更强、下跌敏感度更低。
3. 至少需要20个上涨日和20个下跌日，窗口不完整时因子为空。
4. 评分 = beta不对称性45% + 质量价值防守30% + 现金质量25%。
5. 30只、20日持有、-20%固定止损；T+1开盘执行。
6. R217研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_beta_asymmetry_120d").is_not_null()
    )