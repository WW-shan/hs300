"""HS300 非金融 SG&A 水平 + OCF 利润率等权复合策略（R231 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_sga_ocf_margin_composite_h20_sl20",
    "name": "HS300非金融成本现金复合",
    "description": "历史沪深300剔除银行和非银金融；SG&A费用率水平与OCF利润率等权50%因子袖，叠加质量价值防守与现金质量；30只、20日持有、-20%止损（R231实验）。",
    "tags": ["SG&A", "OCF", "经营现金流", "复合因子", "成本质量", "质量", "现金质量", "沪深300", "非金融", "实验", "R231"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_sga_ratio_4q_low": 0.225,
        "ext_alt_hs300_ocf_margin_4q": 0.225,
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
    "ext_alt_hs300_sga_ratio_4q_low",
    "ext_alt_hs300_ocf_margin_4q",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 复合因子 = SG&A费用率水平反向50% + OCF利润率50%；两个组件都必须非空。
3. 策略评分 = 复合因子45%（两个组件各22.5%）+ 质量价值防守30% + 现金质量25%。
4. SG&A组件衡量成本纪律；OCF利润率组件衡量收入转现金效率。
5. 30只、20日持有、-20%固定止损；T+1开盘执行。
6. R231研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_sga_ratio_4q_low").is_not_null()
        & pl.col("ext_alt_hs300_ocf_margin_4q").is_not_null()
    )