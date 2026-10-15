"""HS300低风险(宽度闸门实验版) — v2 因子集 + 只在市场宽度健康时开新仓。

研究用途 (R11): 检验"低波动/小市值因子在制度逆风年"能否用市场宽度闸门(池内 close>ma20 占比)缓解。
闸门只阻止**开新仓**, 已有持仓仍按 20 日换出/无止损离场。threshold 由 params.breadth_min 控制。

**R11 判定: 不采纳, 本文件仅留作实验记录。** 闸门确实把近一年从 -14.3% 削到 -2.7%~-5.0%,
但全历史阈值扫描 (0.4~0.7) 非单调、无平台 (0.6 只是噪声峰), 闸门均值 Sharpe ~0.87 低于无闸门的
v2 (1.02) 且总收益大降 —— 削峰来自低暴露, 不是稳定 alpha。详见
.iterations/ai_hs300_multi_factor_rank/LEDGER.md 的 R11 一节。
"""
import polars as pl

META = {
    "id": "ai_hs300_lowrisk_gated",
    "name": "HS300低风险(宽度闸门实验)",
    "description": "v2 三元组评分 + 仅当池内 close>ma20 占比 >= 阈值 时允许开新仓 (研究用)",
    "tags": ["多因子", "低波动", "流动性", "沪深300", "闸门实验"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 500,
        "market_cap_min": 10e8,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {"id": "breadth_min", "label": "宽度闸门(close>ma20占比)", "type": "float",
         "default": 0.35, "min": 0.0, "max": 0.9, "step": 0.05},
    ],
    "scoring": {
        "uf_low_amount": 0.34,
        "uf_small_mv": 0.33,
        "uf_low_vol": 0.33,
    },
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = None
MAX_HOLD_DAYS = 20

REQUIRED_FEATURES = {"ma20", "ma60", "annual_vol_20d"}

RULES = """
1. 与 ai_hs300_lowrisk_alpha 相同的三元组评分 (低成交额/小市值/低波动)。
2. 额外闸门: 只有当当日池内 收盘价>MA20 的个股占比 >= breadth_min 时才允许开新仓。
3. 已有持仓不受闸门影响, 按 20 日强制换出, 无固定止损。
4. 本策略为研究实验版, 用于检验市场宽度闸门对制度逆风年的作用。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    breadth_min = float(params.get("breadth_min", 0.35))
    breadth = (pl.col("close") > pl.col("ma20")).cast(pl.Float64).mean().over("date")
    return pl.col("ma60").is_not_null() & (breadth >= breadth_min)
