"""HS300 非金融毛利率稳定性 + 质量价值 + 现金质量复合策略（R223 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_gross_margin_stability_h20_sl20",
    "name": "HS300非金融毛利率稳定性",
    "description": "历史沪深300剔除银行和非银金融；4季毛利率稳定性、质量价值防守与现金质量复合；20日持有、-20%止损（R223实验）",
    "tags": ["毛利率", "稳定性", "定价权", "成本控制", "质量", "现金质量", "沪深300", "非金融", "实验", "R223"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_gross_margin_stability_4q_low": 0.45,
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
    "ext_alt_hs300_gross_margin_stability_4q_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 累计营业收入和营业成本分别还原为单季值；Q1直接使用报告值，后续季度减去上一季度累计值。
3. 单季毛利率 =（单季营业收入 - 单季营业成本）/ 单季营业收入，要求单季营收大于0。
4. 因子 = 最近连续4季毛利率总体标准差（ddof=0）取负；高值代表毛利率更稳定。
5. 评分 = 毛利率稳定性45% + 质量价值防守30% + 现金质量25%。
6. 30只、20日持有、-20%固定止损；信号日使用截至当日已披露的法定回退财务数据，T+1开盘执行。
7. R223研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_gross_margin_stability_4q_low").is_not_null()
    )