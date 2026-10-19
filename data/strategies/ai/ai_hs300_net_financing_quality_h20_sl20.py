"""HS300 净筹资强度反向 + 质量价值 + 现金质量复合策略（R218 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_net_financing_quality_h20_sl20",
    "name": "HS300净筹资强度质量",
    "description": "4季净筹资现金流/总资产反向、质量价值防守与现金质量复合；20日持有、-20%止损（R218实验）",
    "tags": ["净筹资现金流", "外部融资", "质量", "现金质量", "沪深300", "实验", "R218"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_net_financing_4q_low": 0.45,
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
    "ext_alt_hs300_net_financing_4q_low",
}
RULES = """
1. 累计净筹资现金流还原为单季值；Q1直接使用报告值，后续季度减去上一季度累计值。
2. 连续4季单季净筹资现金流求和，除以最新期末总资产，再取负。
3. 高值代表外部融资依赖更低；评分 = 净筹资强度反向45% + 质量价值防守30% + 现金质量25%。
4. 30只、20日持有、-20%固定止损；信号日使用截至当日已披露的法定回退财务数据，T+1开盘执行。
5. R218研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_net_financing_4q_low").is_not_null()
    )