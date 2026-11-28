"""HS300 cash-safety 九因子，14% 组合波动目标候选。

研究链路：R537C stable-seven + R550 财务安全腿 + R552C 参数冻结。
本策略把研究分数接入标准矩阵策略；仓位波动目标和行业上限由回测配置显式选择。
"""
from app.strategy.research.factor_rank_research import FactorRankResearchMatrixStrategy

META = {
    "research_only": True,
    "id": "ai_hs300_cash_safety_v14",
    "name": "HS300现金安全14%波动目标",
    "description": "R550 cash_safety 九因子，10仓/60日/-12%止损/行业上限3/14%波动目标（研究候选）",
    "tags": ["财务", "质量", "价值", "低波", "沪深300", "研究候选"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 300,
        "market_cap_min": 1_000_000_000.0,
        "amount_min": 20_000_000.0,
        "exclude_st": True,
        "exclude_new_days": 30,
        "boards": ["沪主板", "深主板", "创业板", "科创板", "北交所"],
    },
    "params": [
        {"id": "entry_score", "label": "入场最低分", "type": "float", "default": 70.0, "min": 0.0, "max": 100.0, "step": 5.0},
        {"id": "exit_score", "label": "离场最高分", "type": "float", "default": 40.0, "min": 0.0, "max": 70.0, "step": 5.0},
        {"id": "top_rank", "label": "每日最多入选", "type": "int", "default": 20, "min": 1, "max": 100, "step": 1},
        {"id": "max_industry_positions", "label": "单行业最多持仓", "type": "int", "default": 3, "min": 0, "max": 10, "step": 1},
    ],
    "scoring": {},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.12
MAX_HOLD_DAYS = 60
REQUIRED_FEATURES = set()

RULES = """
1. 评分因子固定为 uf_r550_cash_safety_rank。
2. 回测参数固定为 10 仓、60 日持有、-12% 止损、单行业最多 3 只。
3. 买入权重选择内置「组合波动目标14%」，无杠杆、最大敞口不超过 100%。
4. 研究候选，不是生产策略；近一年和全周期结果必须重新回测确认。
"""

MATRIX_STRATEGY = FactorRankResearchMatrixStrategy(
    scoring={"uf_r550_cash_safety_rank": 1.0},
    directions={"uf_r550_cash_safety_rank": "high"},
)
