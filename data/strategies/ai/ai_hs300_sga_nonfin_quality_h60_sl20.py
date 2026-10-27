"""HS300 非金融 SG&A 费用率反向 + 质量价值 + 现金质量复合策略（R240 60日持有研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_sga_nonfin_quality_h60_sl20",
    "name": "HS300非金融费用率效率质量60日",
    "description": "历史沪深300剔除银行和非银金融；4季销售+管理费用率反向、质量价值防守与现金质量复合；30只评分池、最多10仓、60日持有、-20%止损（R240低换手实验）。",
    "tags": ["SG&A", "费用率", "运营效率", "质量", "现金质量", "沪深300", "非金融", "实验", "R240", "低换手"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_sga_ratio_4q_low": 0.45,
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
MAX_HOLD_DAYS = 60
REQUIRED_FEATURES = {
    "ma60",
    "industry_l1",
    "ext_alt_hs300_sga_ratio_4q_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 累计销售费用、管理费用和营业收入分别还原为单季值；Q1直接使用报告值，后续季度减去上一季度累计值。
3. 连续4季求和得到TTM销售费用、管理费用和营业收入。
4. 因子 = -(TTM销售费用 + TTM管理费用) / TTM营业收入；高值代表费用率更低。
5. 评分 = SG&A费用率反向45% + 质量价值防守30% + 现金质量25%。
6. 30只评分池、最多10仓、60日持有、-20%固定止损；T+1开盘执行。
7. R240低换手实验，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_sga_ratio_4q_low").is_not_null()
    )