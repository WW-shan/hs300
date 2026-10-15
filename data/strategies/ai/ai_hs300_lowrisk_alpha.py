"""HS300 低风险超额策略 — 成交额 / 市值 / 波动率 三维截面排名(低关注度 + 小市值 + 低波动)。

思路: 在本仓库 2021-06 ~ 2024-12 的 HS300 全因子研究中(月度 5 分组), 一致跨年为正的
不是动量而是"低关注度 + 低风险"类因子; 把三个互相独立的方向做等权截面排名:
  uf_low_amount  = 0 - log(成交额 + 1)         低成交额(低关注度)
  uf_small_mv    = 0 - log(收盘 x 成交量 / 换手率)  流通市值小(指数内小盘)
  uf_low_vol     = 0 - annual_vol_20d          20日年化波动低

依赖: 三个 uf_* 是 **自定义公式因子**(DSL),定义在 data/user_data/custom_factors/*.json,
不在 backend 源码里。这三个 json 已随仓库版本化 (commit ec7ac49), 但清空 `data/` 后仍会缺失 ——
缺失时策略评分会静默退化。使用前请确认因子列表里能看到 uf_*。

同类静默退化还有**数据侧**一种 (2026-10-15 独立复跑实测): 若 enriched 管道运行的时点早于标的维表
(缺 float_shares), enriched 不含 `turnover_rate` 列, `uf_small_mv` 会被静默丢弃, 策略只剩 2 条腿照常
出结果 (同一配置 2021-06 起 +226% → +54%, 无任何报错)。诊断: 回测返回的
`factor_attribution.factors` 应含 uf_low_amount / uf_small_mv / uf_low_vol 三条腿, 缺腿即命中。
修复顺序: 先同步标的维表 + 历史股本, 再全量重算 enriched。

不做趋势过滤: 该类因子的多头是"回调 / 冷门"股, 用 close>MA20 反而会剔掉信号。

回测口径与结论 (R2~R11 完整迭代见 .iterations/ai_hs300_multi_factor_rank/LEDGER.md):
  - 10 相位平均 8.3 年 (2018-06-01~2026-10-14): 总收益 +387% (区间 +282%~+532%), Sharpe 1.02,
    最大回撤 -24.7%, 8 年 7 年正超额 (2018H2~2024 全为正, 唯 2025 -2.1pp); 沪深300 同期 +11.9%。
  - 2× 成本 (佣金 4bp + 滑点 10bp) 后 8.3 年 +299% (基准成本 +361%); 蒙卡 (20 日块重抽样) 回撤
    中位 -21.1% / P05 -33.6% —— 回测的 -18.4% 不是实盘预期, 应按 -25%~-34% 准备承受力。
  - **近一年 (2025-10-14 空仓重新起跑) 前 5 个相位: -14.3 / -9.9 / -5.6 / -9.4 / -2.9%,
    全负**(尾部长跑口径同为全负)。小市值腿 (log_float_mv) 方向翻转、低成交额腿失效, 只剩低波动
    仍在工作 —— 属因子制度逆风, 不是实现或数据 bug。滚动一年口径仍有 93.5% 为正 (P05 -1.8%),
    当前正处在那 6.5% 的尾部。
  - 所有"修近一年"的廉价方案均已被事前门槛否决: 隔夜融合 (C1/C2)、降换手 (hold=30)、
    加反转腿 (M1/M2)、市场宽度闸门 (见 ai_hs300_lowrisk_gated.py)。不要重复试错。

风险: 单相位误差 ±10pp 量级; qfq 数据修订会移动历史数字; 整段回测按"当前"HS300 名单过滤
(幸存者偏差); 未做实盘/模拟盘验证。本策略不能承诺当前 regime 下的稳定 alpha。
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