"""AData 内置数据源 provider。

仅提供 stock instruments 与不复权原始日K。方法签名对齐 custom.GenericHTTPProvider,
由 custom loader 注册后复用现有 service 路由与 Parquet 写入链路。
日K契约(plugin-development.md): 存储层只放不复权原始价, 前复权由 adj_factor +
enriched 管道统一处理; AData 无除权因子数据集, 该能力由其他源按能力路由补齐。

AData 的免费上游接口在异常/限流时可能返回空 DataFrame 而不是抛异常, 且字段名
随版本漂移。本 adapter 对响应做严格校验: 非空响应无法解析、缺少必要字段、
日期无法解析或整列数值为空时抛出 ADataProviderError, 避免上层把坏响应当成
"成功的 0 行同步"。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

import polars as pl

import adata
from adata.common.utils import requests as adata_requests
from app.data_providers.normalizer import normalize_daily, to_polars

logger = logging.getLogger(__name__)

_DATASETS = ("daily",)
_EXCHANGES = {"SH", "SZ", "BJ"}
# AData 2.9.5 exchange_suffix 的前两位映射(含 A/B 股)。仅接受能唯一确定交易所的
# 股票代码, 基金/债券/指数等代码在请求 SDK 前即拒绝, 防止错标成股票数据。
_EXCHANGE_BY_PREFIX = {
    "00": "SZ",
    "20": "SZ",
    "30": "SZ",
    "43": "BJ",
    "60": "SH",
    "68": "SH",
    "83": "BJ",
    "87": "BJ",
    "90": "SH",
    "92": "BJ",
}
_DAILY_REQUIRED = ("symbol", "date", "open", "high", "low", "close", "volume", "amount")
_DAILY_VALUE_COLUMNS = ("open", "high", "low", "close", "volume", "amount")
_DAILY_SYMBOL_BATCH_SIZE = 50
_ADATA_REQUEST_TIMEOUT = (3.05, 8.0)


class _ADataRequestAdapter:
    """Bound AData SDK network calls and retain swallowed request failures per thread."""

    def __init__(self, request):
        self._request = request
        self._state = threading.local()

    def request(self, *args, **kwargs):
        self._state.error = None
        kwargs.setdefault("timeout", _ADATA_REQUEST_TIMEOUT)
        try:
            response = self._request(*args, **kwargs)
        except Exception as exc:
            self._state.error = exc
            raise

        status = getattr(response, "status_code", None)
        if status is not None and status not in (200, 404):
            self._state.error = RuntimeError(f"AData HTTP {status}")
        return response

    def clear_error(self) -> None:
        self._state.error = None

    def take_error(self) -> Exception | None:
        error = getattr(self._state, "error", None)
        self._state.error = None
        return error


def _install_adata_request_adapter() -> _ADataRequestAdapter:
    adapter = getattr(adata_requests, "_hs300_request_adapter", None)
    if isinstance(adapter, _ADataRequestAdapter):
        return adapter

    adapter = _ADataRequestAdapter(adata_requests.request)
    adata_requests.request = adapter.request
    adata_requests._hs300_request_adapter = adapter
    return adapter


_ADATA_REQUEST_ADAPTER = _install_adata_request_adapter()


class ADataProviderError(RuntimeError):
    """AData 响应无法满足内部数据契约; 上层应据此失败并提示。"""


@dataclass(frozen=True)
class _ADataConfig:
    """轻量 config shim, 供 loader 判断内置插件的数据集能力。"""

    name: str = "adata"
    display_name: str = "AData"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def availability() -> tuple[bool, str]:
    """依赖已随基础环境安装时即可注册; 不在启动阶段访问外部接口。"""
    try:
        import adata as adata_sdk  # noqa: F401
    except Exception as exc:
        return False, f"AData 依赖不可用: {exc}"
    return True, "ok"


def _is_empty_payload(raw) -> bool:
    """判断 SDK 返回值是否为空; 仅用于区分空数据与"非空但解析失败"。"""
    if raw is None:
        return True
    if isinstance(raw, (list, tuple, dict, set)):
        return len(raw) == 0
    empty = getattr(raw, "empty", None)
    return isinstance(empty, bool) and empty


def _to_frame(raw, *, context: str) -> pl.DataFrame:
    """严格转换 SDK 响应: 非空却得不到表格说明响应结构已漂移, 必须失败。"""
    if _is_empty_payload(raw):
        return pl.DataFrame()
    try:
        frame = to_polars(raw)
    except Exception as exc:
        raise ADataProviderError(
            f"{context}响应无法解析 (type={type(raw).__name__}): {exc}"
        ) from exc
    if frame.is_empty():
        raise ADataProviderError(f"{context}响应非空但无法解析为表格 (type={type(raw).__name__})")
    return frame


def _normalize_code(raw) -> str | None:
    """把 AData 的股票代码归一为 6 位字符串; 非股票/非数字代码返回 None。"""
    text = str(raw if raw is not None else "").strip()
    if text.endswith(".0"):  # pandas 浮点读入 600519.0 的兼容路径
        text = text[:-2]
    if not text.isdigit():
        return None
    code = text.zfill(6)
    if len(code) != 6 or code[:2] not in _EXCHANGE_BY_PREFIX:
        return None
    return code


def _resolve_symbol(symbol: str) -> tuple[str, str]:
    """返回 (AData 6 位代码, panel 标准代码), 并校验后缀与代码前缀一致。"""
    text = str(symbol or "").strip().upper()
    suffix = ""
    if "." in text:
        head, _, tail = text.rpartition(".")
        if tail in _EXCHANGES:
            text, suffix = head, tail
    if text[:2] in _EXCHANGES:
        suffix = suffix or text[:2]
        text = text[2:]
    code = _normalize_code(text)
    if code is None:
        raise ADataProviderError(f"AData 不支持该股票代码: {symbol!r}")
    expected = _EXCHANGE_BY_PREFIX[code[:2]]
    if suffix and suffix != expected:
        raise ADataProviderError(f"AData 股票代码与交易所不匹配: {symbol!r}")
    return code, f"{code}.{expected}"


def _date_arg(value: datetime | date | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError as exc:
            raise ADataProviderError(f"AData 日期参数非法: {value!r}") from exc
    raise ADataProviderError(f"AData 日期参数类型不支持: {type(value).__name__}")


class ADataProvider:
    """AData 股票维表与不复权原始日K适配器。"""

    name = "adata"
    builtin = True

    def __init__(self) -> None:
        self.config = _ADataConfig()

    def close(self) -> None:
        pass

    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        if asset_type != "stock":
            return []
        frame = _to_frame(adata.stock.info.all_code(), context="AData 股票列表")
        if frame.is_empty():
            # 空维表不是合法的市场状态; 抛给 instrument_sync, 由其告警并回退 TickFlow。
            raise ADataProviderError("AData 股票列表返回空响应, 无法建立标的维表")
        missing = [col for col in ("stock_code", "exchange") if col not in frame.columns]
        if missing:
            raise ADataProviderError(f"AData 股票列表响应缺少字段: {', '.join(missing)}")

        rows: list[dict] = []
        for item in frame.to_dicts():
            code = _normalize_code(item.get("stock_code"))
            exchange = str(item.get("exchange") or "").strip().upper()
            if code is None or exchange not in _EXCHANGES:
                continue
            if _EXCHANGE_BY_PREFIX[code[:2]] != exchange:
                continue
            rows.append(
                {
                    "symbol": f"{code}.{exchange}",
                    "name": str(item.get("short_name") or "").strip() or code,
                    "code": code,
                    "exchange": exchange,
                    "region": "CN",
                    "type": "stock",
                }
            )
        if not rows:
            raise ADataProviderError(f"AData 股票列表 {frame.height} 行均无法识别为有效股票代码")
        return rows

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | date | str | None,
        end_time: datetime | date | str | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        frames = list(
            self.iter_daily(
                symbols,
                start_time,
                end_time,
                asset_type=asset_type,
                on_chunk_done=on_chunk_done,
            )
        )
        return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | date | str | None,
        end_time: datetime | date | str | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        if not symbols or asset_type != "stock":
            return

        total = len(symbols)
        start_date = _date_arg(start_time)
        end_date = _date_arg(end_time)
        for batch_start in range(0, total, _DAILY_SYMBOL_BATCH_SIZE):
            batch_frames: list[pl.DataFrame] = []
            batch_end = min(batch_start + _DAILY_SYMBOL_BATCH_SIZE, total)
            for index in range(batch_start, batch_end):
                current = index + 1
                code, panel_symbol = _resolve_symbol(symbols[index])
                _ADATA_REQUEST_ADAPTER.clear_error()
                # adjust_type=0: 存储层必须是不复权原始价, 自行前复权会与
                # adj_factor 复权链二次复权, 且污染 raw_close 的涨跌停判定。
                raw = adata.stock.market.get_market(
                    stock_code=code,
                    start_date=start_date,
                    end_date=end_date,
                    k_type=1,
                    adjust_type=0,
                )
                request_error = _ADATA_REQUEST_ADAPTER.take_error()
                if request_error is not None:
                    if on_chunk_done:
                        on_chunk_done(current, total)
                    logger.warning(
                        "AData 日K请求失败, 停止后续标的: symbol=%s error=%s",
                        panel_symbol,
                        request_error,
                    )
                    if batch_frames:
                        yield pl.concat(batch_frames, how="diagonal_relaxed")
                    return
                frame = self._normalize_bars(raw, panel_symbol)
                if on_chunk_done:
                    on_chunk_done(current, total)
                if not frame.is_empty():
                    batch_frames.append(frame)
            if batch_frames:
                yield pl.concat(batch_frames, how="diagonal_relaxed")

    def get_adj_factors(self, *args, **kwargs) -> pl.DataFrame:
        # 未声明 adj_factor 数据集: 能力矩阵会路由到独立复权因子源, 不在此伪造因子。
        return pl.DataFrame()

    def get_minute(self, *args, **kwargs) -> pl.DataFrame:
        return pl.DataFrame()

    def get_realtime(self, *args, **kwargs) -> pl.DataFrame:
        return pl.DataFrame()

    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        return {}

    @staticmethod
    def _normalize_bars(raw, symbol: str) -> pl.DataFrame:
        frame = _to_frame(raw, context=f"AData 日K[{symbol}]")
        if frame.is_empty():
            logger.debug("AData 日K空响应: symbol=%s", symbol)
            return frame
        if "trade_time" in frame.columns:
            if "trade_date" in frame.columns:
                frame = frame.drop("trade_date")
            frame = frame.rename({"trade_time": "trade_date"})
        elif "trade_date" not in frame.columns:
            raise ADataProviderError(f"AData 日K[{symbol}]响应缺少 trade_time/trade_date 字段")
        frame = frame.with_columns(
            pl.col("trade_date")
            .cast(pl.Utf8, strict=False)
            .str.slice(0, 10)
            .str.to_date(strict=False)
            .alias("trade_date")
        )
        frame = normalize_daily(frame, default_symbol=symbol, source="adata")
        missing = [col for col in _DAILY_REQUIRED if col not in frame.columns]
        if missing:
            raise ADataProviderError(f"AData 日K[{symbol}]响应缺少必要字段: {', '.join(missing)}")
        if frame.is_empty():
            return frame
        if frame["date"].null_count():
            raise ADataProviderError(f"AData 日K[{symbol}]响应包含无法解析的日期")
        for col in _DAILY_VALUE_COLUMNS:
            if frame[col].null_count() == frame.height:
                raise ADataProviderError(f"AData 日K[{symbol}]响应字段整列为空: {col}")
        # 以请求的 panel symbol 为准, 防止上游 schema 漂移把行挂到其他标的。
        frame = frame.with_columns(pl.lit(symbol).alias("symbol"))
        # AData 日K volume 单位为股, 项目日K契约统一为手。
        return frame.with_columns((pl.col("volume") / 100).alias("volume"))
