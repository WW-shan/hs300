"""HS300 行业内管理费用率 + OCF 利润率 + 毛利率趋势最弱一环策略（R254 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_industry_management_ocf_gm_minrank_quality_h20_sl20",
    "name": "HS300行业内质量最弱一环",
    "description": "历史沪深300剔除银行和非银金融；同日同行业内管理费用率反向、OCF利润率与毛利率同比改善的横截面分位取最小值，单股行业使用中性0.5，叠加质量价值防守与现金质量；30只、10仓、20日持有、-20%止损（R254实验）。",
    "tags": ["管理费用", "OCF", "毛利率", "最弱一环", "复合因子", "质量", "沪深300", "非金融", "实验", "R254"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_indminrank_industry_management_ocf_gm_min_rank": 0.45,
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
    "ext_alt_hs300_indminrank_industry_management_ocf_gm_min_rank",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子 = 管理费用率反向、OCF利润率、毛利率同比改善三项同日同行业内横截面分位的最小值；单股行业使用中性0.5。
3. 三项都必须非空；不使用未来财报、未来收益或全样本排名。
4. 评分 = 最弱一环45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R254研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_indminrank_industry_management_ocf_gm_min_rank").is_not_null()
    )