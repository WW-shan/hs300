"""HS300 非金融营运资本同比改善 + 质量价值 + 现金质量复合策略（R242 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_working_capital_change_quality_h20_sl20",
    "name": "HS300非金融营运资本同比改善",
    "description": "历史沪深300剔除银行和非银金融；营运资本占用同比改善、质量价值防守与现金质量复合；30只评分池、20日持有、-20%止损（R242实验）",
    "tags": ["营运资本", "应收账款", "存货", "应付账款", "资产负债表", "质量", "沪深300", "非金融", "实验", "R242"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_wc_working_capital_change_yoy_low": 0.45,
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
    "industry_l1",
    "ext_alt_hs300_wc_working_capital_change_yoy_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. NWC = 应收账款 + 存货 - 应付账款；因子 = clip(一年前NWC/总资产 - 当前NWC/总资产, -1, 1)。
3. 使用当前报告期法定披露截止日+3日；必须存在恰好四个季度前的报告期，缺失不补0、不插值、不回退公告日。
4. 评分 = 营运资本同比改善45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R242研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_wc_working_capital_change_yoy_low").is_not_null()
    )