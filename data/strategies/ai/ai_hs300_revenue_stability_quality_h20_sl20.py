"""HS300 4 季度营收增长稳定性 + 质量价值 + 现金质量复合策略（R250 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_revenue_stability_quality_h20_sl20",
    "name": "HS300营收增长稳定性",
    "description": "历史沪深300剔除银行和非银金融；四个季度单季营收同比增速的稳定性、质量价值防守与现金质量复合；30只评分池、10仓、20日持有、-20%止损（R250 实验）。",
    "tags": ["营收稳定性", "需求可见度", "基本面稳定", "质量", "沪深300", "非金融", "实验", "R250"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_revstab_revenue_growth_stability_4q_low": 0.45,
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
    "ext_alt_hs300_revstab_revenue_growth_stability_4q_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子为最近四个单季营收同比增速标准差的相反数；增长越稳定得分越高，不奖励增长水平或短期加速。
3. 单季营收由累计YTD报表还原；必须存在连续四个有效同比增速，任一缺失则整项为空；超过500天置空。
4. 评分 = 营收增长稳定性45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R250研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_revstab_revenue_growth_stability_4q_low").is_not_null()
    )