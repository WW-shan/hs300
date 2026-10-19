"""HS300 非金融 SG&A 费用率反向 + 质量价值 + 现金质量复合策略（R228 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_sga_industry_neutral_h20_sl20",
    "name": "HS300非金融行业内费用率分位",
    "description": "历史沪深300剔除银行和非银金融；行业内SG&A费用率分位、质量价值防守与现金质量复合；20日持有、-20%止损（R228实验）",
    "tags": ["SG&A", "费用率", "运营效率", "质量", "现金质量", "沪深300", "非金融", "实验", "R228"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_sga_ratio_4q_industry_neutral": 0.45,
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
    "ext_alt_hs300_sga_ratio_4q_industry_neutral",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 累计销售费用、管理费用和营业收入分别还原为单季值；Q1直接使用报告值，后续季度减去上一季度累计值。
3. 基于R221的4季TTM SG&A费用率反向值，在同一交易日、同一行业内计算平均秩百分位。
4. 因子 = (行业内平均秩 - 1) / (行业内有效成员数 - 1)；高值代表相对同行业费用率更低。
5. 评分 = 行业内SG&A费用率分位45% + 质量价值防守30% + 现金质量25%。
6. 30只、20日持有、-20%固定止损；信号日使用截至当日已披露的法定回退财务数据，T+1开盘执行。
7. R228研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_sga_ratio_4q_industry_neutral").is_not_null()
    )