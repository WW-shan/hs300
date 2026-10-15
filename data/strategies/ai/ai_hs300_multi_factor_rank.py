"""HS300 多因子截面排名策略 (研究台账中的 v1) — 在沪深300当前成分内按多因子综合评分选股。

默认因子组合: 20日隔夜收益动量(正向) + 振幅趋势 20/60(正向)。
引擎按评分在当日候选池内做截面排序, 取评分最高者等权建仓; 持仓持有至
止损或最长持有到期, 空出的仓位由当日最高分标的补足。

因子来源: HS300 全因子批量回测 (2025-10-14 ~ 2026-10-13, 月度 5 分组)。
  overnight_ret_20d      IC +0.104  IR +0.711  多空 +0.194
  amplitude_trend_20_60  IC +0.093  IR +0.904  多空 +0.178

回测口径与结论 (细节见 .iterations/ai_hs300_multi_factor_rank/LEDGER.md):
  - 单路径近一年 (2025-10-14~2026-10-13, max_positions=10, 止损 -20%, 持有 20 日):
    +32.6%, 最大回撤 -23.8%, Sharpe 1.10 —— 这是特定数据版本 + 特定建仓相位的单次抽样,
    不代表期望值 (台账实测单相位误差 ±10pp)。
  - 10 相位平均 8.3 年 (2018-06-01~2026-10-14): 总收益 +273.0%, Sharpe 0.66, 最大回撤 -40.5%,
    8 年 6 年正超额。动量类回撤深是本策略的固有属性。
  - 近一年 (2025-10-14 起 5 个相位, **空仓重新起跑**): +8.0/+2.4/+6.7/-18.4/-0.0%
    (中位 +2.4%), 有 1 个相位为负; 同一窗口下 ai_hs300_lowrisk_alpha 是 5/5 全负。
    (台账 R10 另有一个"尾部 252 交易日"口径 = +4.5%~+21.7% 全正, 那是从 2018 年起长跑组合
    上截取尾部窗口, **不等于**空仓起跑近一年 —— 两者结论不同, 不要混用。)
  - 当前制度的更稳部署形态是提高分散度: max_positions=20 时近一年 5/5 相位为正 (中位 +14.8%,
    2× 成本后 +13.3%), 全历史 +198% / Sharpe 0.64 / 最大回撤 -40%。注意 max_positions 是
    回测与监控的配置项, 不在本文件里。

风险: 动量策略回撤深、相位敏感; 整段回测按"当前"HS300 名单过滤 (幸存者偏差);
qfq 数据修订会移动历史数字; 未做实盘/模拟盘验证。
"""

import polars as pl

META = {
    "id": "ai_hs300_multi_factor_rank",
    "name": "HS300多因子截面排名",
    "description": "沪深300成分内按隔夜收益动量与振幅趋势的截面综合评分排序, 买入评分最高的个股等权持有, 止损+最长持有离场",
    "tags": ["多因子", "截面排名", "动量", "沪深300"],
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
        {
            "id": "use_trend_filter",
            "label": "启用趋势过滤(收盘价站上MA20)",
            "type": "bool",
            "default": True,
        },
    ],
    "scoring": {
        "overnight_ret_20d": 0.5,
        "amplitude_trend_20_60": 0.5,
    },
    "order_by": "score",
    "descending": True,
    "limit": 50,
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.20
MAX_HOLD_DAYS = 20

REQUIRED_FEATURES = {"ma20", "ma60", "overnight_ret_20d", "amplitude_trend_20_60"}

RULES = """
1. 候选池: 沪深300当前成分股, 剔除 ST、上市不足 60 日、低价低流动性与小市值标的。
2. 数据预热: 至少要有 60 个交易日的行情 (MA60 非空), 保证评分因子可计算;
   数据不足的个股与区间不参与, 避免在因子为空时用 0 分乱排序。
3. 趋势过滤(默认开启): 仅保留收盘价站上 MA20 的个股, 避免在下跌趋势中接刀。
4. 评分: 对当日候选池内的 overnight_ret_20d(20日隔夜收益) 与
   amplitude_trend_20_60(20/60日振幅趋势) 做截面归一后等权加权, 得到 0~100 分。
5. 建仓: 每个交易日按评分从高到低取前若干名, 等权买入, 至多用满 max_positions 个仓位。
6. 离场: 触发止损 -20% 或持有满 20 个交易日; 空出的仓位由当日最高分标的补足。
7. A 股 T+1: T 日收盘产生信号, T+1 开盘成交。
"""


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    """候选层: 数据预热 + 可选趋势过滤 (收盘价站上 MA20)。评分由 META.scoring 负责。"""
    history_ok = pl.col("ma60").is_not_null()
    if not bool(params.get("use_trend_filter", True)):
        return history_ok
    return history_ok & (pl.col("close") > pl.col("ma20"))