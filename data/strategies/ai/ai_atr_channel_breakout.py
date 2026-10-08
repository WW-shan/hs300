"""ATR 通道突破(双环境闸门 + 环境离场版) — 突破入场 + 环境过滤 + 环境转弱离场

入场: 收盘价上穿 MA20 + KxATR14 上轨, 且个股 MA20 > MA60、量比达标。
环境: 沪深300 收盘价站上其 MA(默认 20 日) 且全市场宽度(站上 MA20 的个股占比)达标,
     仅在市场整体向上时开仓。
离场: ① 收盘价跌破 MA20 - KxATR14 通道下轨; ② 可选的环境离场(默认关闭): 指数跌破其均线
     或市场宽度跌破离场下限时全部持仓次日开盘离场; 引擎另按 STOP_LOSS/MAX_HOLD_DAYS 兜底。
"""

import numpy as np

from app.backtest.matrix import (
    MarketDataMatrix,
    SignalMatrix,
    make_signal_matrix,
    matrix_feature,
    valid_shift,
)
from app.strategy.market_data import get_index_daily

META = {
    "id": "ai_atr_channel_breakout",
    "name": "ATR通道突破",
    "description": "上穿 MA20+KxATR14 上轨(趋势+量能确认), 沪深300 与市场宽度双重环境闸门, 跌破通道下轨或环境转弱时离场",
    "tags": ["ATR", "突破", "趋势", "环境过滤"],
    "asset_types": ["stock", "etf"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 5,
        "price_max": 300,
        "market_cap_min": 20e8,
        "amount_min": 1e8,
        "exclude_st": True,
        "exclude_new_days": 60,
    },
    "params": [
        {
            "id": "entry_atr_mult",
            "label": "入场通道 ATR 倍数",
            "type": "float",
            "default": 1.25,
            "min": 0.2,
            "max": 3.0,
            "step": 0.05,
        },
        {
            "id": "exit_atr_mult",
            "label": "离场缓冲 ATR 倍数",
            "type": "float",
            "default": 0.75,
            "min": 0.0,
            "max": 5.0,
            "step": 0.25,
        },
        {"id": "require_uptrend", "label": "要求 MA20>MA60", "type": "bool", "default": True},
        {"id": "use_volume_filter", "label": "启用量比过滤", "type": "bool", "default": True},
        {
            "id": "vol_ratio_min",
            "label": "最低量比",
            "type": "float",
            "default": 1.2,
            "min": 0.5,
            "max": 5.0,
            "step": 0.1,
        },
        {"id": "use_index_filter", "label": "启用指数趋势过滤(沪深300)", "type": "bool", "default": True},
        {
            "id": "index_ma_window",
            "label": "指数均线窗口",
            "type": "int",
            "default": 20,
            "min": 5,
            "max": 60,
            "step": 5,
        },
        {"id": "use_regime_filter", "label": "启用市场宽度过滤", "type": "bool", "default": True},
        {
            "id": "regime_ma_window",
            "label": "市场宽度均线窗口",
            "type": "int",
            "default": 20,
            "min": 10,
            "max": 120,
            "step": 5,
        },
        {
            "id": "regime_breadth_min",
            "label": "市场宽度下限",
            "type": "float",
            "default": 0.4,
            "min": 0.2,
            "max": 0.9,
            "step": 0.05,
        },
        {"id": "use_regime_exit", "label": "环境转弱强制离场(可选)", "type": "bool", "default": False},
        {
            "id": "regime_exit_index_ma",
            "label": "离场指数均线窗口(0=关闭指数腿)",
            "type": "int",
            "default": 0,
            "min": 0,
            "max": 60,
            "step": 5,
        },
        {
            "id": "regime_exit_breadth_min",
            "label": "离场宽度下限(0=关闭宽度腿)",
            "type": "float",
            "default": 0.35,
            "min": 0.0,
            "max": 0.9,
            "step": 0.05,
        },
    ],
    "scoring": {"momentum_20d": 0.4, "vol_ratio_5d": 0.3, "change_pct": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "matrix_native"
ENTRY_SIGNALS = ["signal_atr_channel_breakout"]
EXIT_SIGNALS = ["signal_atr_channel_breakdown", "signal_regime_exit"]
STOP_LOSS = -0.08
MAX_HOLD_DAYS = 30

RULES = """
1. 入场: 收盘价上穿 MA20 + K_entry x ATR14 上轨 (波动率自适应通道突破事件, 非持续状态)。
2. 个股确认: MA20 > MA60 (不做下跌趋势中的反弹), 量比 ≥ 阈值 (放量确认)。
3. 指数环境: 沪深300 收盘价站上其 MA20; 大盘走弱时不开新仓 (T 日收盘判定, T+1 开盘成交)。
4. 市场宽度: 全市场收盘价站上 MA20 的个股占比 ≥ 40%, 剔除多数个股走弱的洗牌阶段。
5. 离场: 收盘价 < MA20 - K_exit x ATR14, 即 ATR 缓冲的通道下轨被击穿。
6. 环境离场: 大盘(沪深300)跌破其均线 或 市场宽度跌破离场下限时, 全部持仓次日开盘离场,
   不等个股各自止损 (两条腿可用参数单独关闭)。
7. 兜底: 引擎固定止损 STOP_LOSS 与最长持有 MAX_HOLD_DAYS 到期强制离场。
8. ATR 用 14 日 Wilder 平滑, 全部基于前复权价; 不含未来数据。
"""


class AtrChannelBreakoutMatrixStrategy:
    def required_fields(self) -> frozenset[str]:
        return frozenset({"close", "high", "low", "volume"})

    def required_warmup_bars(self, params: dict) -> int:
        del params
        return 60

    def _index_regime_ok(self, market: MarketDataMatrix, window: int) -> np.ndarray:
        labels = [str(label)[:10] for label in market.timestamp_labels]
        index_ok = np.ones(market.shape[0], dtype=bool)
        frame = get_index_daily("000300.SH", start=labels[0], end=labels[-1])
        if frame.is_empty() or "close" not in frame.columns:
            return index_ok
        ma_column = f"ma{window}"
        if ma_column not in frame.columns:
            return index_ok
        close_by_date = {
            str(day)[:10]: float(close)
            for day, close in zip(frame["date"].to_list(), frame["close"].to_list())
            if close is not None
        }
        ma_by_date = {
            str(day)[:10]: float(ma)
            for day, ma in zip(frame["date"].to_list(), frame[ma_column].to_list())
            if ma is not None
        }
        for row, label in enumerate(labels):
            index_close = close_by_date.get(label)
            index_ma = ma_by_date.get(label)
            index_ok[row] = bool(
                index_close is not None
                and index_ma is not None
                and np.isfinite(index_close)
                and np.isfinite(index_ma)
                and index_close > index_ma
            )
        return index_ok

    def _market_breadth(self, market: MarketDataMatrix, window: int) -> np.ndarray:
        """全市场收盘价站上 MA{window} 的个股占比 (矩阵内截面)。"""
        ma = matrix_feature(market, f"ma{window}")
        close = market.close
        valid = np.isfinite(ma) & np.isfinite(close)
        above = valid & (close > ma)
        return np.divide(
            above.sum(axis=1),
            valid.sum(axis=1),
            out=np.zeros(market.shape[0], dtype=np.float32),
            where=valid.sum(axis=1) > 0,
        )

    def compute_signals(self, market: MarketDataMatrix, params: dict) -> SignalMatrix:
        entry_mult = float(params.get("entry_atr_mult", 1.25))
        exit_mult = float(params.get("exit_atr_mult", 0.75))

        atr = matrix_feature(market, "atr_14")
        ma20 = matrix_feature(market, "ma20")
        close = market.close
        finite = np.isfinite(atr) & np.isfinite(ma20) & np.isfinite(close)

        upper = ma20 + np.float32(entry_mult) * atr
        lower = ma20 - np.float32(exit_mult) * atr
        prev_close = valid_shift(close, 1, finite)
        prev_upper = valid_shift(upper, 1, finite)
        entry = (
            finite
            & np.isfinite(prev_close)
            & np.isfinite(prev_upper)
            & (close > upper)
            & (prev_close <= prev_upper)
        )
        if params.get("require_uptrend", True):
            entry &= ma20 > matrix_feature(market, "ma60")
        if params.get("use_volume_filter", True):
            entry &= matrix_feature(market, "vol_ratio_5d") >= float(
                params.get("vol_ratio_min", 1.2)
            )

        use_index_filter = bool(params.get("use_index_filter", True))
        use_regime_filter = bool(params.get("use_regime_filter", True))
        use_regime_exit = bool(params.get("use_regime_exit", False))
        index_window = int(params.get("index_ma_window", 20))
        index_ok = None
        if use_index_filter or use_regime_exit:
            index_ok = self._index_regime_ok(market, index_window)
        if use_index_filter:
            entry &= index_ok[:, None]

        breadth = None
        if use_regime_filter or use_regime_exit:
            breadth = self._market_breadth(
                market, int(params.get("regime_ma_window", 20))
            )
        if use_regime_filter:
            entry &= (
                breadth >= np.float32(float(params.get("regime_breadth_min", 0.4)))
            )[:, None]

        breakdown = finite & (close < lower)
        regime_exit = np.zeros(market.shape[0], dtype=bool)
        if use_regime_exit:
            # 环境转弱: 指数跌破其均线 或 市场宽度跌破下限 → 全部持仓次日开盘离场。
            exit_index_window = int(params.get("regime_exit_index_ma", 0))
            if exit_index_window > 0:
                exit_index_ok = (
                    index_ok
                    if exit_index_window == index_window and index_ok is not None
                    else self._index_regime_ok(market, exit_index_window)
                )
                regime_exit |= ~exit_index_ok
            exit_breadth_min = float(params.get("regime_exit_breadth_min", 0.35))
            if exit_breadth_min > 0 and breadth is not None:
                regime_exit |= breadth < np.float32(exit_breadth_min)

        exit_ = breakdown | regime_exit[:, None]
        return make_signal_matrix(
            market.shape,
            entry=entry.astype(np.uint8),
            exit=exit_.astype(np.uint8),
            entry_signal_code=np.where(entry, 0, -1).astype(np.int16),
            exit_signal_code=np.where(
                regime_exit[:, None], 1, np.where(breakdown, 0, -1)
            ).astype(np.int16),
            entry_signal_ids=("signal_atr_channel_breakout",),
            exit_signal_ids=("signal_atr_channel_breakdown", "signal_regime_exit"),
        )


MATRIX_STRATEGY = AtrChannelBreakoutMatrixStrategy()
