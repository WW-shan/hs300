"""交易日探针 (oracle) — 回答「今天是否 A 股交易日」。

消费方 (实时行情轮询 / 盘中分钟增量) 在周几+时段门控之后调用, 用于把
「工作日但休市」的节假日从轮询窗口里剔除; 返回 None (未知) 时调用方
维持现状行为 (周几近似 + 快照新鲜度判据兜底), 不引入新依赖。

探测链 (按确定性排序, 先到先得):
  1. fuyao 交易日历 (已配置 fuyao 时): GET /api/a-share/calendar/trading-days,
     今天在近一年交易日列表内 ⇔ 交易日。权威日历, 无时段依赖, 无开盘缓冲问题。
  2. 当前日K/实时/分钟数据源的可选交易日历 (如 AkShare Sina 日历), 适用于工作日节假日。
  3. tickflow 实时行情时间戳: 拉一篮流动性票快照 (单请求), max(timestamp)
     日期 == 今天 ⇔ 交易日。非交易日全市场戳停在上一交易日 (2026-08-29 周六
     实测 5551/5551, 含停牌股 — 戳是快照定版时刻, 非最后成交时刻);
     交易日集合竞价阶段 (9:15-9:30) 戳是否已翻新未实测 → 开盘缓冲窗内
     戳过期不作数, 保守视为未知。
  4. 均不可用 → None: 调用方按周几近似继续。

安全约束:
  - 周末直接返回 False (周几判断零成本, 不打任何请求)。
  - 探针是纯读: 只产出一个布尔判定, 不落盘、不进行情管道、不碰归属链路。
  - 只用于「降档」(休市不轮询); 休市结论 TTL 较短 (30 分钟) 定期复探,
    探针误判最坏损失一段快照且可自愈; 未知结论短 TTL (5 分钟) 防止
    轮询循环每拍重打失败的探测。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time

from app.market_time import CN_TZ, cn_now

# tickflow 戳探针的开盘缓冲窗: 此时刻之前戳仍是上一交易日属正常 (集合竞价),
# 不据此判休市。周一实测竞价戳翻新时机后可收紧。仅上午首个窗口需要。
_STALE_BUFFER_UNTIL = dt_time(9, 40)

# 一篮流动性票: 探 max(timestamp), 任一戳为今日即交易日 (OR 语义)。
# 大盘蓝筹同日全部停牌 = 市场性事件, 与休市同处理无碍。
_BASKET = ("000001.SZ", "600519.SH", "600036.SH", "601318.SH", "000651.SZ")

_TTL_TRADING_S = 3600.0   # 交易日结论每小时复探 (跨日天然失效)
_TTL_HOLIDAY_S = 1800.0   # 休市结论 30 分钟复探, 误判自愈上限
_TTL_UNKNOWN_S = 300.0    # 未知结论 5 分钟后重试探测

_CACHE_LOCK = threading.Lock()
_CALENDAR_CACHE_LOCK = threading.Lock()
_CALENDAR_CACHE_TTL_S = 6 * 60 * 60
_CALENDAR_CACHE: dict[str, tuple[float, frozenset[date]]] = {}


@dataclass
class _Cache:
    day: object | None = None
    verdict: bool | None = None
    probed_at: float = 0.0


_CACHE = _Cache()


def reset_cache() -> None:
    """清空探针缓存 (测试用)。"""
    with _CACHE_LOCK:
        _CACHE.day = None
        _CACHE.verdict = None
        _CACHE.probed_at = 0.0
    with _CALENDAR_CACHE_LOCK:
        _CALENDAR_CACHE.clear()


def _calendar_provider_names() -> list[str]:
    """优先使用当前数据源的交易日历, Fuyao 作为最后的日历候选。"""
    from app.services import preferences

    names: list[str] = []
    for getter_name in (
        "get_daily_data_provider",
        "get_realtime_data_provider",
        "get_minute_data_provider",
    ):
        try:
            name = str(getattr(preferences, getter_name)() or "").strip().lower()
        except Exception:
            continue
        if name and name != "tickflow" and name not in names:
            names.append(name)
    if "fuyao" not in names:
        names.append("fuyao")
    return names


def _provider_trading_days(name: str) -> set[date] | None:
    """Load an optional provider calendar, caching successful responses for six hours."""
    now = time.monotonic()
    with _CALENDAR_CACHE_LOCK:
        cached = _CALENDAR_CACHE.get(name)
        if cached and now - cached[0] < _CALENDAR_CACHE_TTL_S:
            return set(cached[1])

    try:
        from app.data_providers import custom as custom_sources

        if not custom_sources.is_custom_provider(name):
            return None
        provider = custom_sources.get_provider(name)
        getter = getattr(provider, "trading_days", None)
        if not callable(getter):
            return None
        raw_days = getter()
        days: set[date] = set()
        for value in raw_days or ():
            if isinstance(value, datetime):
                days.add(value.date())
            elif isinstance(value, date):
                days.add(value)
            else:
                try:
                    days.add(date.fromisoformat(str(value).strip()[:10]))
                except ValueError:
                    continue
        if not days:
            return None
    except Exception:  # calendar is advisory; callers fail open on gaps
        return None

    with _CALENDAR_CACHE_LOCK:
        _CALENDAR_CACHE[name] = (time.monotonic(), frozenset(days))
    return days


def trading_days_between(start: date, end: date) -> set[date] | None:
    """Return confirmed trading dates in an inclusive range, or None if no calendar works.

    Missing-day integrity checks must use a real calendar. Returning None distinguishes
    an unavailable calendar from a valid range with no trading sessions.
    """
    for name in _calendar_provider_names():
        days = _provider_trading_days(name)
        if days is not None:
            return {day for day in days if start <= day <= end}
    return None


def _probe_fuyao(now: datetime) -> bool | None:
    """Fuyao 交易日历: 今天在列表内 ⇔ 交易日。未配置 / 失败 → None。"""
    try:
        from app.data_providers import custom as custom_sources

        if not custom_sources.is_custom_provider("fuyao"):
            return None
        provider = custom_sources.get_provider("fuyao")
        days = provider.trading_days()
        return now.date() in days if days else None
    except Exception:  # noqa: BLE001 — 探针失败按未知处理, 不上抛
        return None


def _probe_configured_calendar(now: datetime) -> bool | None:
    """Use the configured data providers' optional calendars for weekday holidays."""
    for name in _calendar_provider_names():
        if name == "fuyao":
            continue  # Fuyao was already probed first to preserve its existing priority.
        days = _provider_trading_days(name)
        if days is not None:
            return now.date() in days
    return None


def _probe_tickflow(now: datetime) -> bool | None:
    """tickflow 行情时间戳: max(timestamp) 日期 == 今天 ⇔ 交易日。

    戳停在上一交易日: 开盘缓冲窗内 → None (可能是竞价未翻新), 窗后 → False。
    无实时权限 / 网络失败 / 无有效戳 → None。
    """
    try:
        from app.tickflow.client import get_client

        rows = get_client().quotes.get(symbols=list(_BASKET)) or []
        stamps = [r.get("timestamp") for r in rows if isinstance(r, dict)]
        valid = [int(t) for t in stamps if isinstance(t, (int, float)) and t]
        if not valid:
            return None
        latest_day = datetime.fromtimestamp(max(valid) / 1000, tz=CN_TZ).date()
        if latest_day == now.date():
            return True
        if now.time() < _STALE_BUFFER_UNTIL:
            return None
        return False
    except Exception:  # noqa: BLE001 — 无权限/网络失败按未知处理
        return None


def is_trading_day(now: datetime | None = None) -> bool | None:
    """今天是否 A 股交易日。True=交易日, False=确定休市, None=未知 (维持周几近似)。

    周末零成本直判; 工作日走探测链 (fuyao / 当前数据源日历 → tickflow 时间戳),
    结论按 TTL 缓存。线程安全: 实时行情与分钟增量两个线程共用。
    """
    now = now or cn_now()
    if now.weekday() >= 5:
        return False

    with _CACHE_LOCK:
        # 「未知」(None) 也是一个结论, 同样按 TTL 缓存 —— 它正是 _TTL_UNKNOWN_S 要
        # 挡住的场景 (没有可用交易日历且 tickflow 不可用时, 避免每拍重打探测)。
        # _CACHE.day 只在探测写回时设置, 因此「当天已探过」用它判定即可。
        if (
            _CACHE.day == now.date()
            and (time.monotonic() - _CACHE.probed_at) < _ttl_of(_CACHE.verdict)
        ):
            return _CACHE.verdict

    verdict = _probe_fuyao(now)
    if verdict is None:
        verdict = _probe_configured_calendar(now)
    if verdict is None:
        verdict = _probe_tickflow(now)

    with _CACHE_LOCK:
        _CACHE.day = now.date()
        _CACHE.verdict = verdict
        _CACHE.probed_at = time.monotonic()
    return verdict


def _ttl_of(verdict: bool | None) -> float:
    if verdict is True:
        return _TTL_TRADING_S
    if verdict is False:
        return _TTL_HOLIDAY_S
    return _TTL_UNKNOWN_S
