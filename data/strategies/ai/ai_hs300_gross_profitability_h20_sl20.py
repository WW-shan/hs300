"""HS300 非金融总资产毛利生产率 + 质量价值 + 现金质量复合策略（R244 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_gross_profitability_h20_sl20",
    "name": "HS300非金融总资产毛利生产率",
    "description": "历史沪深300剔除银行和非银金融；TTM毛利/总资产、质量价值防守与现金质量复合；30只评分池、20日持有、-20%止损（R244实验）",
    "tags": ["毛利生产率", "毛利", "总资产", "资产效率", "质量", "沪深300", "非金融", "实验", "R244"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_gp_gross_profitability": 0.45,
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
    "ext_alt_hs300_gp_gross_profitability",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 累计营业收入和营业成本还原为单季值；Q1直接使用报告值，后续季度减上一季度累计值。
3. 因子 = (TTM营业收入 - TTM营业成本) / 当前总资产；高值代表更高资产毛利生产率。
4. 要求连续四个完整季度；缺失不补0、不插值。
5. 评分 = 毛利生产率45% + 质量价值防守30% + 现金质量25%。
6. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
7. R244研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_gp_gross_profitability").is_not_null()
    )