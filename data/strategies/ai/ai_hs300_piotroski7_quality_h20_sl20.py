"""HS300 七项会计质量分数 + 质量价值 + 现金质量复合策略（R247 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_piotroski7_quality_h20_sl20",
    "name": "HS300七项会计质量分数",
    "description": "历史沪深300剔除银行和非银金融；七项年度会计质量改善、质量价值防守与现金质量复合；30只评分池、10仓、20日持有、-20%止损（R247 实验）。",
    "tags": ["Piotroski", "会计质量", "盈利改善", "现金流", "杠杆", "资产周转", "沪深300", "非金融", "实验", "R247"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_pf7_piotroski7_score": 0.45,
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
    "ext_alt_hs300_pf7_piotroski7_score",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 七项年度信号各占1/7：ROA>0、OCF>0、ROA改善、OCF>净利润、杠杆下降、毛利率改善、资产周转改善。
3. 只用连续两期年报；任一组件缺失则整项为空，不用部分和替代；超过500天置空。
4. 评分 = 七项质量分数45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R247研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_pf7_piotroski7_score").is_not_null()
    )