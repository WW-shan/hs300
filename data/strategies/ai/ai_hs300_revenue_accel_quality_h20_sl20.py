"""HS300 单季营收增速加速 + 质量价值 + 现金质量复合策略（R248 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_revenue_accel_quality_h20_sl20",
    "name": "HS300单季营收增速加速",
    "description": "历史沪深300剔除银行和非银金融；单季营收同比增速相对上一季度加速，并与质量价值防守和现金质量复合；30只评分池、10仓、20日持有、-20%止损（R248 实验）。",
    "tags": ["营收加速", "需求动量", "基本面动量", "质量", "沪深300", "非金融", "实验", "R248"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_rga_revenue_growth_accel_q": 0.45,
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
    "ext_alt_hs300_rga_revenue_growth_accel_q",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子 = 当前单季营收同比增速 - 上一季度单季营收同比增速；同比消除季节，差值衡量需求动量加速。
3. 单季营收由累计YTD报表还原；必须存在相邻季度及各自去年同期观测，任一组件缺失则整项为空；超过500天置空。
4. 评分 = 营收增速加速45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R248研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_rga_revenue_growth_accel_q").is_not_null()
    )