"""当前沪深300成分识别 — 以中证指数官方接口为准。

这是系统中唯一的 HS300 名单来源: 每次筛选按官方最新成分动态解析, 不再读取
任何月度快照归档, 也不存在固定 300 池。官方接口不可用时按以下顺序降级:

1. 6 小时内的本地缓存 (同一轮交易时段内不重复打网络);
2. 过期缓存 (仅当官方接口暂时不可用, 返回的 `as_of` 仍会透出给上层);
3. 全部不可用即 fail-closed 抛错, 不回退到历史快照。

来源顺序: 中证指数官网 (csindex, 权威) -> 东方财富 (em, 备用)。
东方财富接口可能返回重复代码, 解析时会按规范化 symbol 去重。
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import polars as pl

from app.config import settings

logger = logging.getLogger(__name__)

# 成分每年 6/12 月调样; 6 小时缓存足以覆盖盘中反复筛选, refresh=true 可强制拉取。
CACHE_TTL_SECONDS = 6 * 60 * 60
_CACHE_LOCK = threading.Lock()


class CurrentMembersError(ValueError):
    """当前沪深300成分无法识别 (官方接口失败且无可用缓存)。"""


@dataclass(frozen=True)
class CurrentMembers:
    as_of: str | None
    source: str  # "csindex" | "em" | "cache"
    fetched_at: str
    members: tuple[dict[str, str], ...]

    @property
    def symbols(self) -> list[str]:
        return [member["symbol"] for member in self.members]

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "count": len(self.members),
            "members": list(self.members),
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_symbol(code: str, exchange: str | None = None) -> str:
    """把官方/东财的 6 位代码规范成本地 `.SH/.SZ/.BJ` 口径。"""
    text = str(code).strip()
    if "." in text:
        base, suffix = text.rsplit(".", 1)
        suffix = suffix.upper()
        if suffix in {"SH", "SS"}:
            return f"{base}.SH"
        if suffix in {"SZ", "BJ"}:
            return f"{base}.{suffix}"
        return text
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 6:
        return text
    exchange_text = (exchange or "").strip()
    if exchange_text.startswith("上海") or digits[0] in {"6", "9"}:
        return f"{digits}.SH"
    if exchange_text.startswith("深圳") or digits[0] in {"0", "2", "3"}:
        return f"{digits}.SZ"
    if exchange_text.startswith("北京") or digits[0] in {"4", "8"}:
        return f"{digits}.BJ"
    return digits


def _fetch_csindex_frame() -> pl.DataFrame:
    import akshare as ak

    return pl.from_pandas(ak.index_stock_cons_csindex(symbol="000300"))


def _fetch_em_frame() -> pl.DataFrame:
    import akshare as ak

    return pl.from_pandas(ak.index_stock_cons(symbol="000300"))


def _members_from_frame(frame: pl.DataFrame, source: str) -> CurrentMembers:
    columns = set(frame.columns)
    if {"成分券代码", "成分券名称"} <= columns:
        code_col, name_col = "成分券代码", "成分券名称"
        exchange_col = "交易所" if "交易所" in columns else None
        as_of_col = "日期" if "日期" in columns else None
    elif {"品种代码", "品种名称"} <= columns:
        code_col, name_col = "品种代码", "品种名称"
        exchange_col = None
        as_of_col = None
    else:
        raise CurrentMembersError(f"未识别的成分字段: {sorted(columns)}")

    codes = frame.get_column(code_col).to_list()
    names = frame.get_column(name_col).to_list()
    exchanges = frame.get_column(exchange_col).to_list() if exchange_col else [None] * len(codes)

    unique: dict[str, str] = {}
    for raw_code, raw_name, raw_exchange in zip(codes, names, exchanges, strict=True):
        symbol = normalize_symbol(raw_code, raw_exchange)
        if symbol and symbol not in unique:
            unique[symbol] = str(raw_name or "").strip()
    if not unique:
        raise CurrentMembersError("成分列表为空")

    as_of: str | None = None
    if as_of_col:
        dates = sorted(
            str(value)[:10]
            for value in frame.get_column(as_of_col).to_list()
            if value is not None
        )
        as_of = dates[-1] if dates else None
    return CurrentMembers(
        as_of=as_of,
        source=source,
        fetched_at=_now_iso(),
        members=tuple({"symbol": symbol, "name": name} for symbol, name in unique.items()),
    )


def fetch_current_members() -> CurrentMembers:
    """按优先级拉取当前成分; 全部失败抛 CurrentMembersError。"""
    errors: list[str] = []
    for label, loader in (("csindex", _fetch_csindex_frame), ("em", _fetch_em_frame)):
        try:
            result = _members_from_frame(loader(), label)
        except Exception as exc:  # 逐个来源降级后统一汇总
            errors.append(f"{label}: {exc}")
            continue
        if result.as_of is None:
            result = CurrentMembers(
                as_of=date.today().isoformat(),
                source=result.source,
                fetched_at=result.fetched_at,
                members=result.members,
            )
        return result
    raise CurrentMembersError("; ".join(errors))


def default_cache_path() -> Path:
    return Path(settings.data_dir) / "hs300" / "current_members.json"


def _read_cache(path: Path) -> CurrentMembers | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    members = raw.get("members")
    if not isinstance(members, list) or not members:
        return None
    try:
        parsed = tuple(
            {"symbol": str(item["symbol"]), "name": str(item.get("name") or "")}
            for item in members
        )
    except (TypeError, KeyError):
        return None
    source = str(raw.get("source") or "cache")
    if source == "snapshot":
        # 旧版本可能写过快照兜底缓存; 不再把固定池当作当前名单。
        return None
    return CurrentMembers(
        as_of=raw.get("as_of"),
        source=source,
        fetched_at=str(raw.get("fetched_at") or ""),
        members=parsed,
    )


def _write_cache(path: Path, result: CurrentMembers) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        logger.warning("HS300 成分缓存写入失败: %s", exc)


def _is_fresh(fetched_at: str, ttl_seconds: int) -> bool:
    try:
        stamp = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return (datetime.now().astimezone() - stamp).total_seconds() < ttl_seconds


def load_current_members(
    *,
    refresh: bool = False,
    cache_path: Path | str | None = None,
    fetcher: Callable[[], CurrentMembers] = fetch_current_members,
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> CurrentMembers:
    """读取当前成分: 新鲜缓存 -> 官方拉取 -> 过期缓存 -> fail-closed。

    这里没有历史快照兜底。官方源和缓存都不可用时必须显式报错, 避免把某个
    历史 300 池误当成当前沪深300继续筛选。
    """
    path = Path(cache_path) if cache_path else default_cache_path()
    with _CACHE_LOCK:
        cached = _read_cache(path)
        if not refresh and cached is not None and _is_fresh(cached.fetched_at, ttl_seconds):
            return cached
        try:
            result = fetcher()
        except Exception as exc:
            if cached is not None:
                logger.warning("HS300 当前成分获取失败, 回退缓存 (%s): %s", cached.as_of, exc)
                return cached
            raise CurrentMembersError(f"HS300 当前成分不可用: {exc}") from exc
        _write_cache(path, result)
        return result


def current_symbols() -> list[str]:
    """官方当前沪深300名单 (`.SH/.SZ/.BJ`), 供筛选/回测限定数据加载边界。"""
    return load_current_members().symbols
