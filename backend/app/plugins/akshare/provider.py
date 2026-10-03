"""AkShare adapter for the panel's normalized China-market data contracts.

The adapter uses Sina for raw bars and quotes, Eastmoney batch reports for financial
statements, and CNINFO share-change announcements. Historical adjustment and share
requests are bounded by the local HS300 snapshot universe where appropriate.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from app.config import resolve_hs300_snapshot_dir, settings
from app.hs300 import HS300MembershipError, HS300Service

logger = logging.getLogger(__name__)

_DATASETS = ("daily", "adj_factor", "financial", "minute", "realtime")
_MIN_HISTORY_PERIOD = date(2021, 3, 31)
_REQUEST_TIMEOUT_SECONDS = 25.0
_REQUEST_MIN_INTERVAL_SECONDS = 0.05
_SINA_MIN_INTERVAL_SECONDS = 0.5
_EVENT_TOLERANCE = 1e-10
_REQUEST_LOCK = threading.Lock()
_SINA_LOCK = threading.Lock()
_LAST_SINA_REQUEST = 0.0

_METRIC_COLUMNS = (
    "eps_basic",
    "revenue",
    "revenue_yoy",
    "revenue_qoq",
    "net_income",
    "net_income_yoy",
    "net_income_qoq",
    "bps",
    "roe",
    "gross_margin",
    "net_margin",
    "operating_cash_per_share",
    "debt_to_asset_ratio",
)

_FINANCIAL_COLUMNS: dict[str, dict[str, str]] = {
    "income": {
        "净利润": "net_income",
        "净利润同比": "net_income_yoy",
        "营业总收入": "revenue",
        "营业总收入同比": "revenue_yoy",
        "营业总支出-营业支出": "operating_costs",
        "营业总支出-销售费用": "selling_expenses",
        "营业总支出-管理费用": "management_expenses",
        "营业总支出-财务费用": "financial_expenses",
        "营业总支出-营业总支出": "total_operating_costs",
        "营业利润": "operating_profit",
        "利润总额": "total_profit",
    },
    "balance_sheet": {
        "资产-货币资金": "cash_and_equivalents",
        "资产-应收账款": "accounts_receivable",
        "资产-存货": "inventory",
        "资产-总资产": "total_assets",
        "负债-应付账款": "accounts_payable",
        "负债-预收账款": "advance_receipts",
        "负债-总负债": "total_liabilities",
        "资产负债率": "debt_to_asset_ratio",
        "股东权益合计": "total_equity",
    },
    "cash_flow": {
        "净现金流-净现金流": "net_cash_change",
        "净现金流-同比增长": "net_cash_change_yoy",
        "经营性现金流-现金流量净额": "net_operating_cash_flow",
        "经营性现金流-净现金流占比": "operating_cash_flow_ratio",
        "投资性现金流-现金流量净额": "net_investing_cash_flow",
        "投资性现金流-净现金流占比": "investing_cash_flow_ratio",
        "融资性现金流-现金流量净额": "net_financing_cash_flow",
        "融资性现金流-净现金流占比": "financing_cash_flow_ratio",
    },
}
_ANNOUNCEMENT_FIELDS = (
    "最新公告日期",
    "公告日期",
    "更新日期",
    "UPDATE_DATE",
)
_FINANCIAL_REQUIRED_COLUMNS = {
    "metrics": {
        "每股净资产",
        "净资产收益率",
        "营业总收入-同比增长",
        "净利润-同比增长",
        "销售毛利率",
    },
    "income": {"净利润", "净利润同比", "营业总收入"},
    "balance_sheet": {"资产-总资产", "负债-总负债"},
    "cash_flow": {"经营性现金流-现金流量净额"},
}


class AkShareProviderError(RuntimeError):
    """Raised when an AkShare response cannot satisfy the project's data contract."""


@dataclass
class _AkShareConfig:
    datasets: dict = field(default_factory=lambda: {name: None for name in _DATASETS})
    path: None = None
    builtin: bool = True


def _akshare():
    """Import AkShare only when this optional provider is actually used."""
    try:
        return importlib.import_module("akshare")
    except Exception as exc:
        raise AkShareProviderError(f"AkShare 依赖不可用: {exc}") from exc


def availability() -> tuple[bool, str]:
    """Provider discovery must not access the network or fail application startup."""
    try:
        if importlib.util.find_spec("akshare") is None:
            return False, "AkShare 尚未安装, 可在数据源设置中安装插件依赖"
    except (ImportError, ValueError):
        return False, "AkShare 尚未安装或无法加载"
    return True, "ok"


def _call_akshare(function: Callable[..., Any], **kwargs):
    """Give AkShare's requests-based endpoints bounded timeouts and gentle pacing."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - AkShare itself depends on requests
        raise AkShareProviderError("AkShare requests 依赖不可用") from exc

    # Several upstream AkShare functions do not pass a timeout to requests.get. Keep
    # this patch scoped and serialized so a stalled upstream cannot hang a sync forever.
    with _REQUEST_LOCK:
        original_get, original_post = requests.get, requests.post
        last_call = [0.0]

        def _bounded_request(original):
            def call(*args, **request_kwargs):
                request_kwargs.setdefault("timeout", _REQUEST_TIMEOUT_SECONDS)
                elapsed = time.monotonic() - last_call[0]
                if last_call[0] and elapsed < _REQUEST_MIN_INTERVAL_SECONDS:
                    time.sleep(_REQUEST_MIN_INTERVAL_SECONDS - elapsed)
                last_call[0] = time.monotonic()
                return original(*args, **request_kwargs)

            return call

        requests.get = _bounded_request(original_get)
        requests.post = _bounded_request(original_post)
        try:
            return function(**kwargs)
        except Exception as exc:
            raise AkShareProviderError(f"AkShare {function.__name__} 请求失败: {exc}") from exc
        finally:
            requests.get, requests.post = original_get, original_post


def _call_sina_symbol(function: Callable[..., Any], **kwargs):
    """Serialize Sina's per-symbol APIs to avoid bursts from full-universe syncs."""
    global _LAST_SINA_REQUEST
    with _SINA_LOCK:
        delay = _SINA_MIN_INTERVAL_SECONDS - (time.monotonic() - _LAST_SINA_REQUEST)
        if _LAST_SINA_REQUEST and delay > 0:
            time.sleep(delay)
        _LAST_SINA_REQUEST = time.monotonic()
        return _call_akshare(function, **kwargs)


def _as_date(value: datetime | date | str | None, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise AkShareProviderError(f"非法日期: {value!r}") from exc


def _today_cn() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _latest_report_period(as_of: date) -> date:
    """Use only a report period whose statutory disclosure window has closed."""
    if as_of.month >= 11:
        return date(as_of.year, 9, 30)
    if as_of.month >= 9:
        return date(as_of.year, 6, 30)
    if as_of.month >= 5:
        return date(as_of.year, 3, 31)
    return date(as_of.year - 1, 12, 31)


def _financial_report_periods(as_of: date) -> list[date]:
    latest = _latest_report_period(as_of)
    ends = ((3, 31), (6, 30), (9, 30), (12, 31))
    periods = [
        date(year, month, day)
        for year in range(_MIN_HISTORY_PERIOD.year, latest.year + 1)
        for month, day in ends
        if date(year, month, day) >= _MIN_HISTORY_PERIOD
        and date(year, month, day) <= latest
    ]
    return periods


def _normalize_symbol(raw: Any) -> str | None:
    text = str(raw if raw is not None else "").strip().upper()
    if not text or text in {"NAN", "NONE", "NAT"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if "." in text:
        code, suffix = text.rsplit(".", 1)
    else:
        code, suffix = text, ""
    if code.endswith(".0"):
        code = code[:-2]
    if not code.isdigit():
        return None
    code = code.zfill(6)
    if len(code) != 6:
        return None
    if code.startswith(("60", "68", "90")):
        exchange = "SH"
    elif code.startswith(("00", "20", "30")):
        exchange = "SZ"
    elif code.startswith(("4", "8", "92")):
        exchange = "BJ"
    else:
        return None
    if suffix and suffix not in {exchange, "SS" if exchange == "SH" else exchange}:
        return None
    return f"{code}.{exchange}"


def _normalize_market_symbol(raw: Any, asset_type: str = "stock") -> str | None:
    """Normalize stock, index, and ETF symbols without guessing across asset types."""
    text = str(raw if raw is not None else "").strip().upper()
    if not text or text in {"NAN", "NONE", "NAT"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]

    prefix_exchange = None
    for prefix, exchange in (("SH", "SH"), ("SZ", "SZ"), ("BJ", "BJ")):
        if text.startswith(prefix) and text[len(prefix) :].isdigit():
            prefix_exchange = exchange
            text = text[len(prefix) :]
            break

    if "." in text:
        code, suffix = text.rsplit(".", 1)
        exchange = "SH" if suffix == "SS" else suffix
        if exchange not in {"SH", "SZ", "BJ"}:
            return None
    else:
        code = text
        exchange = prefix_exchange

    if not code.isdigit():
        return None
    code = code.zfill(6)
    if len(code) != 6:
        return None
    if exchange is None:
        if asset_type == "stock":
            return _normalize_symbol(code)
        if asset_type == "index":
            exchange = "SZ" if code.startswith("399") else "BJ" if code.startswith("899") else "SH"
        elif asset_type == "etf":
            exchange = "SH" if code.startswith(("50", "51", "52", "56", "58", "59")) else "SZ"
        else:
            return None
    return f"{code}.{exchange}"


def _market_source_code(symbol: str, asset_type: str) -> str:
    normalized = _normalize_market_symbol(symbol, asset_type)
    if normalized is None:
        raise AkShareProviderError(f"无法识别 {asset_type} 标的代码: {symbol!r}")
    code, exchange = normalized.split(".", 1)
    return f"{'sh' if exchange == 'SH' else 'sz' if exchange == 'SZ' else 'bj'}{code}"


def _as_datetime(
    value: datetime | date | str | None,
    default: datetime,
    *,
    end: bool = False,
) -> datetime:
    if value is None:
        return default
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.max.time() if end else datetime.min.time())
    else:
        text = str(value).strip()
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            parsed = _as_date(text, default.date())
            result = datetime.combine(parsed, datetime.max.time() if end else datetime.min.time())
    if result.tzinfo is not None:
        result = result.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return result


def _volume_in_hands(value: Any) -> int | None:
    shares = _number(value)
    return math.floor(shares / 100.0) if shares is not None else None


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _sina_symbol(symbol: str) -> str:
    code, exchange = symbol.split(".", 1)
    return f"{'sh' if exchange == 'SH' else 'sz'}{code}"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "").replace("%", "")
        if not value or value in {"-", "--", "—", "N/A"}:
            return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "--"}:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _records(frame: Any, *, context: str) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, pl.DataFrame):
        return frame.to_dicts()
    if getattr(frame, "empty", False):
        return []
    if hasattr(frame, "to_dict"):
        try:
            rows = frame.to_dict(orient="records")
        except TypeError:
            rows = frame.to_dicts() if hasattr(frame, "to_dicts") else None
        if isinstance(rows, list):
            return rows
    raise AkShareProviderError(f"{context}响应不是可识别的表格: {type(frame).__name__}")


def _hs300_symbols_for_range(
    start_time: datetime | date | str | None,
    end_time: datetime | date | str | None,
) -> set[str]:
    """Resolve the PIT membership union locally; no current constituents are guessed."""
    from app.hs300.service import MIN_SNAPSHOT_DATE

    today = _today_cn()
    start = max(_as_date(start_time, MIN_SNAPSHOT_DATE), MIN_SNAPSHOT_DATE)
    end = min(_as_date(end_time, today), today)
    if end < start:
        return set()
    root = resolve_hs300_snapshot_dir(settings.hs300_snapshot_dir)
    try:
        members = HS300Service(Path(root)).members_between(start, end)
    except HS300MembershipError as exc:
        raise AkShareProviderError(f"无法安全解析 HS300 历史成分: {exc}") from exc
    return set(members["symbol"].unique().to_list())


def _factor_rows(frame: Any, symbol: str) -> list[dict[str, Any]]:
    rows = _records(frame, context=f"{symbol} qfq-factor")
    if not rows:
        raise AkShareProviderError(f"{symbol} qfq-factor 响应为空")
    columns = set(rows[0])
    factor_col = next((c for c in ("qfq_factor", "factor") if c in columns), None)
    if "date" not in columns or factor_col is None:
        raise AkShareProviderError(
            f"{symbol} qfq-factor 响应缺少 date/qfq_factor 字段: {sorted(columns)}"
        )

    normalized: dict[date, float] = {}
    for row in rows:
        event_date = _iso_date(row.get("date"))
        factor = _number(row.get(factor_col))
        if event_date is None or factor is None or factor <= 0:
            raise AkShareProviderError(f"{symbol} qfq-factor 存在非法日期或因子")
        normalized[date.fromisoformat(event_date)] = factor
    ordered = sorted(normalized.items())
    events: list[dict[str, Any]] = []
    for (_, previous), (event_date, current) in pairwise(ordered):
        if event_date > _today_cn():
            continue
        # Sina's cumulative qfq multiplier declines across a corporate-action
        # date; the panel contract stores the reciprocal pre/post event ratio.
        event_factor = previous / current
        if abs(event_factor - 1.0) <= _EVENT_TOLERANCE:
            continue
        events.append(
            {"symbol": symbol, "trade_date": event_date, "ex_factor": event_factor}
        )
    return events


def _financial_endpoint(ak: Any, table: str) -> Callable[..., Any]:
    endpoint_name = {
        "metrics": "stock_yjbb_em",
        "income": "stock_lrb_em",
        "balance_sheet": "stock_zcfz_em",
        "cash_flow": "stock_xjll_em",
    }.get(table)
    if endpoint_name is None:
        raise AkShareProviderError(f"不支持的财务表: {table}")
    endpoint = getattr(ak, endpoint_name, None)
    if endpoint is None:
        raise AkShareProviderError(f"当前 AkShare 版本缺少 {endpoint_name}")
    return endpoint


def _canonical_financial_row(
    row: dict[str, Any], table: str, symbol: str, period: date
) -> dict[str, Any]:
    announce = next(
        (
            row.get(field)
            for field in _ANNOUNCEMENT_FIELDS
            if _iso_date(row.get(field)) is not None
        ),
        None,
    )
    output: dict[str, Any] = {
        "symbol": symbol,
        "period_end": period.isoformat(),
        # Never substitute period_end for an unavailable disclosure date.
        "announce_date": _iso_date(announce),
    }
    if table == "metrics":
        mappings = {
            "每股收益": "eps_basic",
            "营业总收入-营业总收入": "revenue",
            "营业总收入-同比增长": "revenue_yoy",
            "营业总收入-季度环比增长": "revenue_qoq",
            "净利润-净利润": "net_income",
            "净利润-同比增长": "net_income_yoy",
            "净利润-季度环比增长": "net_income_qoq",
            "每股净资产": "bps",
            "净资产收益率": "roe",
            "每股经营现金流量": "operating_cash_per_share",
            "销售毛利率": "gross_margin",
        }
    else:
        mappings = _FINANCIAL_COLUMNS[table]
    for source, target in mappings.items():
        output[target] = _number(row.get(source))
    if table == "metrics":
        revenue = output.get("revenue")
        net_income = output.get("net_income")
        output["net_margin"] = (
            net_income / revenue * 100.0
            if revenue not in (None, 0) and net_income is not None
            else None
        )
    return output


def _validate_financial_rows(rows: list[dict[str, Any]], table: str, period: date) -> None:
    if not rows:
        return
    columns = set().union(*(row.keys() for row in rows))
    if "股票代码" not in columns:
        raise AkShareProviderError(f"AkShare {table} {period} 响应缺少股票代码字段")
    if not columns.intersection(_ANNOUNCEMENT_FIELDS):
        raise AkShareProviderError(f"AkShare {table} {period} 响应缺少公告日期字段")
    missing = _FINANCIAL_REQUIRED_COLUMNS[table] - columns
    if missing:
        raise AkShareProviderError(
            f"AkShare {table} {period} 响应缺少必要字段: {', '.join(sorted(missing))}"
        )


class AkShareProvider:
    """AkShare implementation of the project's existing provider contract."""

    name = "akshare"
    builtin = True

    def __init__(self) -> None:
        self.config = _AkShareConfig()

    def close(self) -> None:
        pass

    def supports_asset_type(self, dataset: str, asset_type: str) -> bool:
        if dataset in {"daily", "minute"}:
            return asset_type in {"stock", "index", "etf"}
        if dataset == "adj_factor":
            return asset_type == "stock"
        return dataset in {"financial", "realtime"} and asset_type in {"stock", "index"}

    def get_instruments(self, asset_type: str = "stock") -> list[dict[str, Any]]:
        """Fetch current instrument symbols and names for stock/index/ETF universes."""
        ak = _akshare()
        if asset_type == "stock":
            frame = _call_akshare(ak.stock_info_a_code_name)
            code_keys, name_keys = ("code", "代码"), ("name", "名称")
        elif asset_type == "index":
            frame = _call_akshare(ak.stock_zh_index_spot_sina)
            code_keys, name_keys = ("代码", "symbol", "code"), ("名称", "name")
        elif asset_type == "etf":
            frame = _call_akshare(ak.fund_etf_spot_ths)
            code_keys, name_keys = ("基金代码", "code"), ("基金名称", "name")
        else:
            raise AkShareProviderError(f"不支持的标的类型: {asset_type}")

        rows = _records(frame, context=f"{asset_type} instruments")
        output: list[dict[str, Any]] = []
        for row in rows:
            code = _first_value(row, *code_keys)
            symbol = _normalize_market_symbol(code, asset_type)
            if symbol is None:
                continue
            name = _first_value(row, *name_keys)
            output.append({
                "symbol": symbol,
                "code": symbol.split(".", 1)[0],
                "exchange": symbol.rsplit(".", 1)[1],
                "name": str(name).strip() if name is not None else symbol,
                "type": asset_type,
            })
        if not output:
            raise AkShareProviderError(f"AkShare {asset_type} 标的维表为空或字段不匹配")
        return sorted(
            {row["symbol"]: row for row in output}.values(),
            key=lambda row: row["symbol"],
        )

    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | date | str | None,
        end_time: datetime | date | str | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """Return unadjusted daily bars; Sina endpoints return volume in shares."""
        if asset_type not in {"stock", "index", "etf"}:
            raise AkShareProviderError(f"不支持的日K标的类型: {asset_type}")
        start = _as_date(start_time, date(1990, 1, 1))
        end = min(_as_date(end_time, _today_cn()), _today_cn())
        if end < start:
            raise AkShareProviderError("日K开始日期晚于结束日期")
        normalized = [
            (source, _normalize_market_symbol(source, asset_type))
            for source in symbols
        ]
        requested = [(source, symbol) for source, symbol in normalized if symbol]
        if not requested:
            return self._daily_frame([])

        ak = _akshare()
        endpoint_name = {
            "stock": "stock_zh_a_daily",
            "index": "stock_zh_index_daily",
            "etf": "fund_etf_hist_sina",
        }[asset_type]
        endpoint = getattr(ak, endpoint_name, None)
        if endpoint is None:
            raise AkShareProviderError(f"当前 AkShare 版本缺少 {endpoint_name}")
        output: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, (_, symbol) in enumerate(requested, start=1):
            try:
                kwargs = {"symbol": _market_source_code(symbol, asset_type)}
                if asset_type == "stock":
                    kwargs.update({
                        "start_date": start.strftime("%Y%m%d"),
                        "end_date": end.strftime("%Y%m%d"),
                        "adjust": "",
                    })
                raw = _call_sina_symbol(endpoint, **kwargs)
                for row in _records(raw, context=f"{symbol} daily"):
                    day = _iso_date(_first_value(row, "date", "日期"))
                    if day is None or not start <= date.fromisoformat(day) <= end:
                        continue
                    values = {
                        key: _number(_first_value(row, key, aliases))
                        for key, aliases in (
                            ("open", "开盘"),
                            ("high", "最高"),
                            ("low", "最低"),
                            ("close", "收盘"),
                        )
                    }
                    if any(value is None for value in values.values()):
                        continue
                    output.append({
                        "symbol": symbol,
                        "date": date.fromisoformat(day),
                        **values,
                        "volume": _volume_in_hands(_first_value(row, "volume", "成交量")),
                        "amount": _number(_first_value(row, "amount", "成交额")),
                    })
            except Exception as exc:
                failures.append(symbol)
                logger.warning("AkShare %s daily failed for %s: %s", asset_type, symbol, exc)
            finally:
                if on_chunk_done:
                    on_chunk_done(index, len(requested))
        if failures and not output:
            raise AkShareProviderError(
                f"AkShare {asset_type} 日K请求全部失败 ({len(failures)} symbols)"
            )
        if failures:
            logger.warning(
                "AkShare %s daily partial failures: %d/%d",
                asset_type,
                len(failures),
                len(requested),
            )
        return self._daily_frame(output)

    @staticmethod
    def _daily_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
        schema = {
            "symbol": pl.String,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        }
        if not rows:
            return pl.DataFrame(schema=schema)
        return pl.DataFrame(rows, schema=schema).unique(
            subset=["symbol", "date"], keep="last"
        ).sort(["symbol", "date"])

    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | date | str | None,
        end_time: datetime | date | str | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
        freq: str = "1m",
    ) -> pl.DataFrame:
        """Fetch Sina's bounded recent intraday window, filtered to requested times."""
        if asset_type not in {"stock", "index", "etf"}:
            raise AkShareProviderError(f"不支持的分钟K标的类型: {asset_type}")
        period = str(freq).lower().removesuffix("m")
        if period not in {"1", "5", "15", "30", "60"}:
            raise AkShareProviderError(f"不支持的 AkShare 分钟频率: {freq}")
        now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        start = _as_datetime(start_time, now - timedelta(days=5))
        end = _as_datetime(end_time, now, end=True)
        if end < start:
            raise AkShareProviderError("分钟K开始时间晚于结束时间")
        requested = [
            symbol for source in symbols
            if (symbol := _normalize_market_symbol(source, asset_type)) is not None
        ]
        schema = {
            "symbol": pl.String,
            "datetime": pl.Datetime("us"),
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "amount": pl.Float64,
        }
        if not requested:
            return pl.DataFrame(schema=schema)
        ak = _akshare()
        endpoint = getattr(ak, "stock_zh_a_minute", None)
        if endpoint is None:
            raise AkShareProviderError("当前 AkShare 版本缺少 stock_zh_a_minute")
        output: list[dict[str, Any]] = []
        failures: list[str] = []
        for index, symbol in enumerate(requested, start=1):
            try:
                raw = _call_sina_symbol(
                    endpoint,
                    symbol=_market_source_code(symbol, asset_type),
                    period=period,
                    adjust="",
                )
                for row in _records(raw, context=f"{symbol} minute"):
                    value = _first_value(row, "day", "datetime", "日期时间")
                    try:
                        stamp = datetime.fromisoformat(str(value).strip())
                    except (TypeError, ValueError):
                        continue
                    if stamp.tzinfo is not None:
                        stamp = stamp.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
                    if not start <= stamp <= end:
                        continue
                    values = {
                        key: _number(_first_value(row, key, alias))
                        for key, alias in (
                            ("open", "开盘"),
                            ("high", "最高"),
                            ("low", "最低"),
                            ("close", "收盘"),
                        )
                    }
                    if any(value is None for value in values.values()):
                        continue
                    output.append({
                        "symbol": symbol,
                        "datetime": stamp,
                        **values,
                        "volume": _volume_in_hands(_first_value(row, "volume", "成交量")),
                        "amount": _number(_first_value(row, "amount", "成交额")),
                    })
            except Exception as exc:
                failures.append(symbol)
                logger.warning("AkShare minute failed for %s: %s", symbol, exc)
            finally:
                if on_chunk_done:
                    on_chunk_done(index, len(requested))
        if failures and not output:
            raise AkShareProviderError(f"AkShare 分钟K请求全部失败 ({len(failures)} symbols)")
        if failures:
            logger.warning("AkShare minute partial failures: %d/%d", len(failures), len(requested))
        if not output:
            return pl.DataFrame(schema=schema)
        return pl.DataFrame(output, schema=schema).unique(
            subset=["symbol", "datetime"], keep="last"
        ).sort(["symbol", "datetime"])

    @staticmethod
    def _realtime_row(row: dict[str, Any], asset_type: str) -> dict[str, Any] | None:
        symbol = _normalize_market_symbol(
            _first_value(row, "代码", "symbol", "code"), asset_type
        )
        last_price = _number(_first_value(row, "最新价", "trade", "last_price"))
        if symbol is None or last_price is None:
            return None
        pct = _number(_first_value(row, "涨跌幅", "changepercent", "change_pct"))
        return {
            "symbol": symbol,
            "name": _first_value(row, "名称", "name"),
            "last_price": last_price,
            "prev_close": _number(_first_value(row, "昨收", "settlement", "prev_close")),
            "open": _number(_first_value(row, "今开", "open")),
            "high": _number(_first_value(row, "最高", "high")),
            "low": _number(_first_value(row, "最低", "low")),
            "volume": _volume_in_hands(_first_value(row, "成交量", "volume")),
            "amount": _number(_first_value(row, "成交额", "amount")),
            "change_pct": pct / 100.0 if pct is not None else None,
            "change_amount": _number(_first_value(row, "涨跌额", "pricechange", "change_amount")),
        }

    def get_realtime(self) -> list[dict[str, Any]]:
        """Fetch Sina's full A-share snapshot; failures soft-return an empty list."""
        try:
            rows = _records(_call_akshare(_akshare().stock_zh_a_spot), context="stock realtime")
            return [
                record for row in rows
                if (record := self._realtime_row(row, "stock")) is not None
            ]
        except Exception as exc:
            logger.warning("AkShare realtime snapshot failed: %s", exc)
            return []

    def get_realtime_indices(self, symbols: list[str]) -> list[dict[str, Any]] | None:
        """Fetch requested Sina index snapshots; None preserves the previous cache on failure."""
        requested = {
            normalized
            for symbol in symbols
            if (normalized := _normalize_market_symbol(symbol, "index")) is not None
        }
        if not requested:
            return []
        try:
            rows = _records(
                _call_akshare(_akshare().stock_zh_index_spot_sina),
                context="index realtime",
            )
        except Exception as exc:
            logger.warning("AkShare index realtime snapshot failed: %s", exc)
            return None
        return [
            record for row in rows
            if (record := self._realtime_row(row, "index")) is not None
            and record["symbol"] in requested
        ]

    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | date | str | None,
        end_time: datetime | date | str | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        if asset_type != "stock":
            return pl.DataFrame(
                schema={"symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64}
            )
        start = _as_date(start_time, date(2023, 7, 1))
        end = min(_as_date(end_time, _today_cn()), _today_cn())
        if end < start:
            raise AkShareProviderError("除权因子开始日期晚于结束日期")
        membership = _hs300_symbols_for_range(start, end)
        requested = {
            normalized
            for symbol in symbols
            if (normalized := _normalize_symbol(symbol)) is not None
        }
        candidates = sorted(requested & membership)
        if not candidates:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return pl.DataFrame(
                schema={"symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64}
            )

        ak = _akshare()
        rows: list[dict[str, Any]] = []
        for index, symbol in enumerate(candidates, start=1):
            try:
                raw = _call_sina_symbol(
                    ak.stock_zh_a_daily,
                    symbol=_sina_symbol(symbol),
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                    adjust="qfq-factor",
                )
                rows.extend(
                    event
                    for event in _factor_rows(raw, symbol)
                    if start <= event["trade_date"] <= end
                )
            except AkShareProviderError:
                raise
            except Exception as exc:
                raise AkShareProviderError(f"AkShare 除权因子 {symbol} 失败: {exc}") from exc
            finally:
                if on_chunk_done:
                    on_chunk_done(index, len(candidates))

        if not rows:
            return pl.DataFrame(
                schema={"symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64}
            )
        return pl.DataFrame(rows).unique(
            subset=["symbol", "trade_date"], keep="last"
        ).sort(["symbol", "trade_date"])

    def get_financials(
        self,
        table: str,
        symbols: list[str],
        latest_only: bool = False,
    ) -> pl.DataFrame:
        if table == "shares":
            return self._get_share_history(symbols, latest_only=latest_only)
        if table not in {"metrics", "income", "balance_sheet", "cash_flow"}:
            raise AkShareProviderError(f"不支持的财务表: {table}")
        requested = {
            normalized
            for symbol in symbols
            if (normalized := _normalize_symbol(symbol)) is not None
        }
        if not requested:
            return pl.DataFrame()

        as_of = _today_cn()
        periods = (
            [_latest_report_period(as_of)]
            if latest_only
            else _financial_report_periods(as_of)
        )
        ak = _akshare()
        endpoint = _financial_endpoint(ak, table)
        rows_out: list[dict[str, Any]] = []
        for period in periods:
            period_key = period.strftime("%Y%m%d")
            raw = _call_akshare(endpoint, date=period_key)
            rows = _records(raw, context=f"{table} {period_key}")
            _validate_financial_rows(rows, table, period)
            debt_ratio: dict[str, float | None] = {}
            if table == "metrics":
                balance_endpoint = _financial_endpoint(ak, "balance_sheet")
                balance_rows = _records(
                    _call_akshare(balance_endpoint, date=period_key),
                    context=f"balance_sheet {period_key}",
                )
                _validate_financial_rows(balance_rows, "balance_sheet", period)
                for balance in balance_rows:
                    symbol = _normalize_symbol(balance.get("股票代码"))
                    if symbol not in requested:
                        continue
                    value = _number(balance.get("资产负债率"))
                    if value is None:
                        assets = _number(balance.get("资产-总资产"))
                        liabilities = _number(balance.get("负债-总负债"))
                        if assets and liabilities is not None:
                            value = liabilities / assets * 100.0
                    if value is not None:
                        debt_ratio[symbol] = value
            for raw_row in rows:
                symbol = _normalize_symbol(raw_row.get("股票代码"))
                if symbol not in requested:
                    continue
                canonical = _canonical_financial_row(raw_row, table, symbol, period)
                if table == "metrics":
                    canonical["debt_to_asset_ratio"] = debt_ratio.get(symbol)
                rows_out.append(canonical)

        if not rows_out:
            return pl.DataFrame()
        if table == "metrics":
            for row in rows_out:
                for column in _METRIC_COLUMNS:
                    row.setdefault(column, None)
        return pl.DataFrame(rows_out, infer_schema_length=None).unique(
            subset=["symbol", "period_end"], keep="last"
        ).sort(["symbol", "period_end"])

    def _get_share_history(
        self,
        symbols: list[str],
        *,
        latest_only: bool,
    ) -> pl.DataFrame:
        requested = sorted({
            normalized
            for symbol in symbols
            if (normalized := _normalize_symbol(symbol)) is not None
        })
        schema = {
            "symbol": pl.String,
            "period_end": pl.String,
            "announce_date": pl.String,
            "total_shares": pl.Float64,
            "float_shares": pl.Float64,
        }
        if not requested:
            return pl.DataFrame(schema=schema)

        from app.hs300.service import MIN_SNAPSHOT_DATE

        hs300_universe = _hs300_symbols_for_range(MIN_SNAPSHOT_DATE, _today_cn())
        requested = sorted(set(requested) & hs300_universe)
        if not requested:
            return pl.DataFrame(schema=schema)

        ak = _akshare()
        endpoint = getattr(ak, "stock_share_change_cninfo", None)
        if endpoint is None:
            raise AkShareProviderError("当前 AkShare 版本缺少 stock_share_change_cninfo")
        end_date = _today_cn()
        rows_out: list[dict[str, Any]] = []
        failures: list[str] = []
        for symbol in requested:
            try:
                raw = _call_akshare(
                    endpoint,
                    symbol=symbol.split(".", 1)[0],
                    start_date="20091227",
                    end_date=end_date.strftime("%Y%m%d"),
                )
                rows = _records(raw, context=f"{symbol} historical shares")
                if rows and not {
                    "证券代码", "变动日期", "公告日期", "总股本", "已流通股份",
                } <= set(rows[0]):
                    raise AkShareProviderError(
                        f"{symbol} 股本变动响应缺少 PIT 字段: {sorted(rows[0])}"
                    )
                for row in rows:
                    normalized = _normalize_symbol(row.get("证券代码"))
                    period_end = _iso_date(row.get("变动日期"))
                    announce_date = _iso_date(row.get("公告日期"))
                    total_shares = _number(row.get("总股本"))
                    float_shares = _number(row.get("已流通股份"))
                    if (
                        normalized != symbol
                        or period_end is None
                        or announce_date is None
                        or total_shares is None
                        or float_shares is None
                        or total_shares <= 0
                        or float_shares <= 0
                    ):
                        continue
                    # CNINFO reports share counts in 10,000 shares; store actual shares.
                    rows_out.append({
                        "symbol": symbol,
                        "period_end": period_end,
                        "announce_date": announce_date,
                        "total_shares": total_shares * 10_000.0,
                        "float_shares": float_shares * 10_000.0,
                    })
            except Exception as exc:
                cause = exc.__cause__
                if (
                    isinstance(exc, AkShareProviderError)
                    and isinstance(cause, KeyError)
                    and cause.args == ("公告日期",)
                ):
                    # AkShare 1.19.1 indexes this column even when CNINFO returns
                    # records=[]; no history is available, so the pipeline can
                    # use the existing instrument-share fallback for this symbol.
                    logger.debug("AkShare CNINFO has no share-history rows for %s", symbol)
                    continue
                failures.append(symbol)
                logger.warning("AkShare historical shares failed for %s: %s", symbol, exc)

        if failures and not rows_out:
            raise AkShareProviderError(
                f"AkShare CNINFO 股本变动请求全部失败 ({len(failures)} symbols)"
            )
        if failures:
            logger.warning(
                "AkShare CNINFO shares partial failures: %d/%d",
                len(failures),
                len(requested),
            )
        if not rows_out:
            return pl.DataFrame(schema=schema)
        frame = pl.DataFrame(rows_out, schema=schema).unique(
            subset=["symbol", "period_end"], keep="last"
        )
        if latest_only:
            frame = (
                frame.sort(["symbol", "announce_date", "period_end"])
                .group_by("symbol", maintain_order=True)
                .tail(1)
            )
        return frame.sort(["symbol", "period_end"])

    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        requested = symbols or ["600000.SH"]
        if dataset == "daily":
            end = _today_cn()
            frame = self.get_daily(
                requested,
                start_time=end - timedelta(days=14),
                end_time=end,
                asset_type="stock",
            )
        elif dataset == "minute":
            end = datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            frame = self.get_minute(
                requested,
                start_time=end - timedelta(days=5),
                end_time=end,
                asset_type="stock",
                freq="1m",
            )
        elif dataset == "realtime":
            records = self.get_realtime()
            frame = pl.DataFrame(records) if records else pl.DataFrame()
        elif dataset == "adj_factor":
            frame = self.get_adj_factors(
                requested,
                start_time=None,
                end_time=None,
                asset_type="stock",
            )
        elif dataset == "financial":
            frame = self.get_financials("metrics", requested, latest_only=True)
        elif dataset == "shares":
            frame = self.get_financials("shares", requested, latest_only=True)
        else:
            return {
                "provider": self.name,
                "dataset": dataset,
                "rows": 0,
                "columns": [],
                "preview": [],
                "error": f"AkShare 未实现 {dataset} 数据集",
            }
        return {
            "provider": self.name,
            "dataset": dataset,
            "rows": frame.height,
            "columns": frame.columns,
            "preview": frame.head(5).to_dicts(),
        }
