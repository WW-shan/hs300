"""HS300 个股低偏度 + 质量价值 + 现金质量复合策略（R249 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_low_stock_skew_quality_h20_sl20",
    "name": "HS300个股低偏度质量",
    "description": "历史沪深300剔除银行和非银金融；60日收益偏度反向、质量价值防守与现金质量复合；30只评分池、10仓、20日持有、-20%止损（R249 实验）。",
    "tags": ["低偏度", "彩票偏好", "尾部风险", "质量", "沪深300", "非金融", "实验", "R249"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_skew_stock_skew_60d_low": 0.45,
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
    "ext_alt_hs300_skew_stock_skew_60d_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子为过去60个交易日收益偏度取反；低正偏或负偏得分更高，捕捉彩票偏好溢价风险。
3. 仅使用截至T日收盘及之前的调整后收盘价，至少60个有效日收益且偏度有限；不足时为空，不补值。
4. 评分 = 个股低偏度45% + 质量价值防守30% + 现金质量25%。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R249研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_skew_stock_skew_60d_low").is_not_null()
    )