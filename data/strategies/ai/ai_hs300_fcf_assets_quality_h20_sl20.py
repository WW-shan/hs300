"""HS300 自由现金流/总资产 + 质量价值 + 现金质量复合策略（R246 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_fcf_assets_quality_h20_sl20",
    "name": "HS300自由现金流资产回报质量",
    "description": "历史沪深300剔除银行和非银金融；年度自由现金流/总资产、质量价值防守与现金质量复合；30只评分池、20日持有、-20%止损（R246实验）",
    "tags": ["自由现金流", "资本开支", "资产回报", "质量", "沪深300", "非金融", "实验", "R246"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_fcf_free_cash_flow_assets": 0.45,
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
    "ext_alt_hs300_fcf_free_cash_flow_assets",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子 = (年度经营现金流 - 年度资本开支) / 总资产；只用年度报告，超过500天置空。
3. 生效日为 max(公告日次日, 法定截止日后3日)；不使用当前名单或未来报告回填。
4. 评分 = 自由现金流/总资产45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R246研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_fcf_free_cash_flow_assets").is_not_null()
    )