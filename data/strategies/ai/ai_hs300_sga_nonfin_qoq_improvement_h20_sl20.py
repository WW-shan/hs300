"""HS300 非金融 SG&A 费用率 TTM 环比改善 + 质量价值 + 现金质量复合策略（R222 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_sga_nonfin_qoq_improvement_h20_sl20",
    "name": "HS300非金融费用率环比改善",
    "description": "历史沪深300剔除银行和非银金融；TTM销售+管理费用率环比改善、质量价值防守与现金质量复合；20日持有、-20%止损（R222实验）",
    "tags": ["SG&A", "费用率", "环比改善", "运营效率", "质量", "现金质量", "沪深300", "非金融", "实验", "R222"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_sga_ratio_4q_qoq_improvement": 0.45,
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
    "ext_alt_hs300_sga_ratio_4q_qoq_improvement",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 累计销售费用、管理费用和营业收入分别还原为单季值，连续4季求和得到TTM值。
3. 当前TTM费用率反向值减去上一季TTM费用率反向值；高值代表SG&A费用率环比下降。
4. 当前和上一季TTM都必须由连续、完整的4个季度构成；缺失不补0，不回退到水平值。
5. 评分 = SG&A费用率环比改善45% + 质量价值防守30% + 现金质量25%。
6. 30只、20日持有、-20%固定止损；信号日使用截至当日已披露的法定回退财务数据，T+1开盘执行。
7. R222研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_sga_ratio_4q_qoq_improvement").is_not_null()
    )