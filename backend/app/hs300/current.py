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
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import polars as pl

from app.config import settings

logger = logging.getLogger(__name__)

# 成分每年 6/12 月调样; 6 小时缓存足以覆盖盘中反复筛选, refresh=true 可强制拉取。
CACHE_TTL_SECONDS = 6 * 60 * 60
HS300_MEMBER_COUNT = 300
# 官方/降级源都必须显式限时; 否则前端 HS300 查询会一直停留在 loading。
HS300_HTTP_TIMEOUT = 15.0
_EM_PAGE_SIZE = 100
_EM_MAX_PAGES = 5
_CACHE_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_REFRESHING_PATHS: set[Path] = set()


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
    """直接读取中证指数官方 XLS, 显式设置网络超时。

    AkShare 对应函数的 requests.get 没有 timeout; 官方源偶发慢连接时会把
    `/api/hs300/current` 卡到浏览器一直 loading。这里保留同一官方 URL, 但由
    本模块控制超时和失败降级。
    """
    import pandas as pd

    url = (
        "https://oss-ch.csindex.com.cn/static/"
        "html/csindex/public/uploads/file/autofile/cons/000300cons.xls"
    )
    with urllib.request.urlopen(url, timeout=HS300_HTTP_TIMEOUT) as response:
        raw = response.read()
    frame = pd.read_excel(BytesIO(raw))
    frame.columns = [
        "日期",
        "指数代码",
        "指数名称",
        "指数英文名称",
        "成分券代码",
        "成分券名称",
        "成分券英文名称",
        "交易所",
        "交易所英文名称",
    ]
    frame["日期"] = pd.to_datetime(
        frame["日期"], format="%Y%m%d", errors="coerce",
    ).dt.date
    frame["指数代码"] = frame["指数代码"].astype(str).str.zfill(6)
    frame["成分券代码"] = frame["成分券代码"].astype(str).str.zfill(6)
    return pl.from_pandas(frame)


def _fetch_em_frame() -> pl.DataFrame:
    """东方财富降级源: 分页读取, 每页请求都显式限时。

    当前 AkShare 的 `index_stock_cons` 实际走新浪分页, 在 HS300 上可能只
    返回 288 只; 这里改用东方财富的指数成分列表接口, 固定分页到 300 只。
    """
    base = "https://push2.eastmoney.com/api/qt/clist/get"
    rows: list[dict] = []
    for page in range(1, _EM_MAX_PAGES + 1):
        params = urllib.parse.urlencode({
            "pn": page,
            "pz": _EM_PAGE_SIZE,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "b:BK0500",
            "fields": "f12,f14",
        })
        with urllib.request.urlopen(f"{base}?{params}", timeout=HS300_HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        data = payload.get("data") or {}
        diff = data.get("diff") or []
        if not diff:
            break
        rows.extend(diff)
        total = int(data.get("total") or len(rows))
        if len(rows) >= total:
            break
    if not rows:
        raise CurrentMembersError("东方财富返回空成分列表")
    return pl.DataFrame({
        "品种代码": [str(row.get("f12") or "") for row in rows],
        "品种名称": [str(row.get("f14") or "") for row in rows],
    })


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
            if len(result.members) != HS300_MEMBER_COUNT:
                raise CurrentMembersError(
                    f"{label} 返回 {len(result.members)} 只, 预期 {HS300_MEMBER_COUNT} 只"
                )
        except Exception as exc:  # noqa: BLE001 — 逐个来源降级后统一汇总
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


def _start_background_refresh(
    path: Path,
    fetcher: Callable[[], CurrentMembers],
) -> None:
    """过期缓存先返回, 官方源在后台刷新; 同一缓存路径最多一个刷新任务。"""
    cache_key = path.resolve()
    with _REFRESH_LOCK:
        if cache_key in _REFRESHING_PATHS:
            return
        _REFRESHING_PATHS.add(cache_key)

    def run() -> None:
        try:
            result = fetcher()
            _write_cache(path, result)
        except Exception as exc:  # noqa: BLE001 — 后台刷新失败不能影响当前请求
            logger.warning("HS300 当前成分后台刷新失败: %s", exc)
        finally:
            with _REFRESH_LOCK:
                _REFRESHING_PATHS.discard(cache_key)

    threading.Thread(
        target=run,
        name="hs300-current-refresh",
        daemon=True,
    ).start()


def load_current_members(
    *,
    refresh: bool = False,
    cache_path: Path | str | None = None,
    fetcher: Callable[[], CurrentMembers] = fetch_current_members,
    ttl_seconds: int = CACHE_TTL_SECONDS,
    background_refresh: bool = True,
) -> CurrentMembers:
    """读取当前成分: 新鲜缓存 -> 过期缓存 + 后台刷新 -> 同步拉取 -> fail-closed。

    过期缓存只用于避免网络抖动把前端卡在 loading; 后台刷新成功后立即写回
    同一份官方名单。refresh=true 时仍同步拉取, 失败才回退缓存。
    这里没有历史快照兜底: 缓存来源必须是 csindex/em, snapshot 会被忽略。
    """
    path = Path(cache_path) if cache_path else default_cache_path()
    cached = _read_cache(path)
    if not refresh and cached is not None:
        if _is_fresh(cached.fetched_at, ttl_seconds):
            return cached
        if background_refresh:
            _start_background_refresh(path, fetcher)
            return cached

    with _CACHE_LOCK:
        # 等待同步锁期间可能已有其他请求写入了新缓存, 再读一次避免重复打网络。
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
