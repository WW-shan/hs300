"""HS300 非金融成分新近度 + 质量价值 + 现金质量复合策略（R245 研究候选）。"""
import polars as pl

META = {
    "id": "ai_hs300_index_tenure_h20_sl20",
    "name": "HS300非金融成分新近度",
    "description": "历史沪深300剔除银行和非银金融；连续成分新近度、质量价值防守与现金质量复合；30只评分池、20日持有、-20%止损（R245实验）",
    "tags": ["成分新近度", "指数纳入", "被动资金", "质量", "沪深300", "非金融", "实验", "R245"],
    "asset_types": ["stock"], "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3, "price_max": 500, "market_cap_min": 10e8,
        "amount_min": 0.2e8, "exclude_st": True, "exclude_new_days": 60,
    },
    "params": [],
    "scoring": {
        "ext_alt_hs300_tenure_hs300_membership_tenure_low": 0.30,
        "uf_quality_value_defensive": 0.3815,
        "uf_cash_quality": 0.3185,
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
    "ext_alt_hs300_tenure_hs300_membership_tenure_low",
}
RULES = """
1. 宇宙为历史沪深300成分，按当前一级行业快照剔除银行和非银金融；行业为空时 fail-closed。
2. 因子 = 1 / (1 + 当前连续成分期天数 / 252)；新纳入或退出后重新纳入时重置起点。
3. 每个交易日只使用 query_date <= 当日的最近成分快照，不使用当前成分表回填。
4. 评分 = 成分新近度30% + 质量价值防守38.15% + 现金质量31.85%，质量/现金保持54.5:45.5。
5. 30只评分池、最多10仓、等权、20日持有、-20%固定止损；T+1开盘执行。
6. R245研究候选，预注册门槛全部通过前不得部署。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df, params
    return (
        pl.col("industry_l1").is_not_null()
        & ~pl.col("industry_l1").is_in(["银行", "非银金融"])
        & pl.col("ma60").is_not_null()
        & pl.col("ext_alt_hs300_tenure_hs300_membership_tenure_low").is_not_null()
    )