"""HS300 低风险超额策略 — 成交额 / 市值 / 波动率 三维截面排名(低关注度 + 小市值 + 低波动)。

思路: 在本仓库 2021-06 ~ 2024-12 的 HS300 全因子研究中(月度 5 分组), 一致跨年为正的
不是动量而是"低关注度 + 低风险"类因子; 把三个互相独立的方向做等权截面排名:
  uf_low_amount  = 0 - log(成交额 + 1)         低成交额(低关注度)
  uf_small_mv    = 0 - log(收盘 x 成交量 / 换手率)  流通市值小(指数内小盘)
  uf_low_vol     = 0 - annual_vol_20d          20日年化波动低

依赖: 三个 uf_* 是 **自定义公式因子**(DSL),定义在 data/user_data/custom_factors/*.json,
不在 backend 源码里。缺少它们时策略评分会静默退化, 使用前请确认因子列表里能看到 uf_*。

不做趋势过滤: 该类因子的多头是"回调 / 冷门"股, 用 close>MA20 反而会剔掉信号。
"""

import polars as pl

META = {
    "id": "ai_hs300_lowrisk_alpha",
    "name": "HS300低风险超额",
    "description": "沪深300成分内按低成交额/小流通市值/低波动三维等权截面排名选股, 等权持有20个交易日",
    "tags": ["多因子", "低波动", "流动性", "沪深300", "截面排名"],
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
    "params": [],
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

REQUIRED_FEATURES = {"ma60", "annual_vol_20d"}

RULES = """
1. 候选池: 沪深300当前成分股, 剔除 ST、上市不足 60 日、低价与小市值标的。
2. 数据预热: 要求 MA60 非空 (至少 60 个交易日行情), 数据不足的个股与区间不参与。
3. 评分: 对 uf_low_amount(低成交额)、uf_small_mv(小流通市值)、uf_low_vol(低波动)
   三个因子各自做当日截面归一后等权加权, 得 0~100 分。三者都已转成"越大越好"。
4. 建仓: 每个交易日按评分从高到低取前若干名等权买入, 用满 max_positions 个仓位。
5. 离场: 持有满 20 个交易日强制换出 (开发区间实测: 这类均值回复因子加固定止损反而卖在低点; -8% ~ -25% 各档止损都拖累收益与回撤, 故默认不设固定止损)。
6. 不做趋势过滤: 该类因子的多头本身是回调/冷门股。
7. A 股 T+1: T 日收盘产生信号, T+1 开盘成交。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """候选层: 仅要求足够历史 (MA60 非空)。评分由 META.scoring 负责。"""
    return pl.col("ma60").is_not_null()