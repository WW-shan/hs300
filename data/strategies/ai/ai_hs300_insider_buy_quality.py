"""HS300 董监高真实竞价增持事件 + 质量价值防守。

实验型事件策略：只在过去 20 个交易日内出现真实竞价增持的股票中选股，
再叠加低估值/高 ROE/低杠杆和低波动。事件源来自 R90 研究缓存，已转换为
PIT 扩展因子 ext_alt_hs300_insider_buy_20d；不宣称已通过可部署门槛。
"""
import polars as pl

META = {
    "id": "ai_hs300_insider_buy_quality",
    "name": "HS300增持事件质量",
    "description": "董监高真实竞价增持事件内，按事件强度+质量价值+低波动选股（实验）",
    "tags": ["事件", "增持", "质量", "价值", "低波动", "沪深300", "实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.2e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {
            "id": "insider_min",
            "label": "最低增持事件强度",
            "type": "float",
            "default": 0.0,
            "min": 0.0,
            "max": 20.0,
            "step": 0.1,
        },
    ],
    "scoring": {
        "ext_alt_hs300_insider_buy_20d": 0.55,
        "uf_quality_value_defensive": 0.25,
        "uf_low_vol": 0.20,
    },
    "order_by": "score",
    "descending": True,
    "limit": 30,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.10
MAX_HOLD_DAYS = 20

REQUIRED_FEATURES = {
    "ma60",
    "ext_alt_hs300_insider_buy_20d",
}

RULES = """
1. 候选池为当前沪深300成分，事件因子必须是最近 20 个交易日内公告的真实竞价增持。
2. 事件强度按公告后交易日衰减，公告日当天不建仓，避免盘后公告前视。
3. 评分 = 事件强度 55% + 质量价值防守 25% + 低波动 20%，均为当日截面标准化。
4. 持有满 20 个交易日强制换出；固定止损 -10%。
5. 实验策略；验证段衰减和幸存者偏差必须在结果中保留。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    del df
    threshold = float(params.get("insider_min", 0.0))
    event = pl.col("ext_alt_hs300_insider_buy_20d")
    return (
        pl.col("ma60").is_not_null()
        & event.is_not_null()
        & (event >= threshold)
    )
