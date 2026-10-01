"""ADataProvider 契约测试。全部使用假 SDK, 不访问网络。"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest


def _provider_module():
    """延迟导入, 让缺失模块表现为测试失败而不是整个测试文件无法收集。"""
    from app.plugins.adata import provider

    return provider


def _daily_frame(stock_code: str, trade_date: str, *, volume: int = 3_000_000) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stock_code": stock_code,
                "trade_time": f"{trade_date} 00:00:00",
                "trade_date": trade_date,
                "open": 1685.0,
                "close": 1690.0,
                "high": 1700.0,
                "low": 1680.0,
                "volume": volume,
                "amount": 5_070_000_000.0,
                "change_pct": 0.3,
                "change": 5.0,
                "turnover_ratio": 0.24,
                "pre_close": 1685.0,
            }
        ]
    )


class _FakeMarket:
    def __init__(
        self, frames: dict[str, pd.DataFrame] | None = None, error: Exception | None = None
    ):
        self.frames = frames or {}
        self.error = error
        self.calls: list[dict] = []

    def get_market(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.frames.get(kwargs["stock_code"], pd.DataFrame())


class _FakeInfo:
    def __init__(self, frame: pd.DataFrame | None = None):
        self.frame = frame if frame is not None else pd.DataFrame()
        self.calls = 0

    def all_code(self):
        self.calls += 1
        return self.frame


def _fake_sdk(market: _FakeMarket, info: _FakeInfo | None = None):
    return SimpleNamespace(
        stock=SimpleNamespace(market=market, info=info or _FakeInfo()),
    )


def test_daily_maps_symbols_and_schema(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket({"600519": _daily_frame("600519", "2023-07-03")})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    df = provider_mod.ADataProvider().get_daily(
        ["600519.SH"],
        datetime(2023, 7, 3),
        datetime(2023, 7, 7),
    )

    assert market.calls == [
        {
            "stock_code": "600519",
            "start_date": "2023-07-03",
            "end_date": "2023-07-07",
            "k_type": 1,
            "adjust_type": 0,
        }
    ]
    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert df.row(0, named=True) == {
        "symbol": "600519.SH",
        "date": date(2023, 7, 3),
        "open": 1685.0,
        "high": 1700.0,
        "low": 1680.0,
        "close": 1690.0,
        "volume": 30_000.0,
        "amount": 5_070_000_000.0,
    }


def test_daily_uses_unadjusted_prices(monkeypatch):
    """内部日K契约要求不复权原始价, 复权由 adj_factor + enriched 管道统一处理。"""
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03")
    raw.loc[0, ["open", "close", "high", "low"]] = [10.0, 10.5, 10.8, 9.9]
    adjusted = _daily_frame("600519", "2023-07-03")
    adjusted.loc[0, ["open", "close", "high", "low"]] = [5.0, 5.25, 5.4, 4.95]

    class _AdjustAwareMarket(_FakeMarket):
        def get_market(self, **kwargs):
            self.calls.append(kwargs)
            return adjusted if kwargs["adjust_type"] == 1 else raw

    market = _AdjustAwareMarket()
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    df = provider_mod.ADataProvider().get_daily(
        ["600519.SH"],
        datetime(2023, 7, 3),
        datetime(2023, 7, 7),
    )

    assert market.calls == [
        {
            "stock_code": "600519",
            "start_date": "2023-07-03",
            "end_date": "2023-07-07",
            "k_type": 1,
            "adjust_type": 0,
        }
    ]
    assert df.row(0, named=True)["close"] == 10.5


def test_iter_daily_yields_bounded_normalized_symbol_batches(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket(
        {
            "600519": _daily_frame("600519", "2023-07-03"),
            "000001": _daily_frame("000001", "2023-07-04"),
            "300750": _daily_frame("300750", "2023-07-05"),
        }
    )
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))
    monkeypatch.setattr(provider_mod, "_DAILY_SYMBOL_BATCH_SIZE", 2, raising=False)
    progress: list[tuple[int, int]] = []

    frames = list(
        provider_mod.ADataProvider().iter_daily(
            ["600519.SH", "000001.SZ", "300750.SZ"],
            datetime(2023, 7, 3),
            datetime(2023, 7, 7),
            on_chunk_done=lambda cur, total: progress.append((cur, total)),
        )
    )

    assert [df["symbol"].to_list() for df in frames] == [
        ["600519.SH", "000001.SZ"],
        ["300750.SZ"],
    ]
    assert [call["stock_code"] for call in market.calls] == ["600519", "000001", "300750"]
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_iter_daily_bounds_sdk_requests_and_stops_after_network_timeout(monkeypatch):
    import importlib

    provider_mod = _provider_module()
    east_market = importlib.import_module(
        "adata.stock.market.stock_market.stock_market_east"
    )
    from adata.common.utils.sunrequests import requests as http_requests

    seen_timeouts = []

    def _timeout_request(*args, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        raise http_requests.Timeout("simulated stalled provider")

    monkeypatch.setattr(http_requests, "request", _timeout_request)

    def _sdk_market_call(**kwargs):
        try:
            east_market.requests.request(method="get", url="http://provider.invalid")
        except http_requests.Timeout:
            # AData's handler_null decorator converts request exceptions to an empty frame.
            return pd.DataFrame()
        raise AssertionError("fake request should time out")

    monkeypatch.setattr(provider_mod.adata.stock.market, "get_market", _sdk_market_call)

    frames = list(
        provider_mod.ADataProvider().iter_daily(
            ["600519.SH", "000001.SZ"],
            date(2023, 7, 3),
            date(2023, 7, 7),
        )
    )

    assert seen_timeouts == [(3.05, 8.0)]
    assert frames == []


def test_instruments_use_panel_suffixes(monkeypatch):
    provider_mod = _provider_module()
    info = _FakeInfo(
        pd.DataFrame(
            [
                {"stock_code": "600519", "short_name": "贵州茅台", "exchange": "SH"},
                {"stock_code": "000001", "short_name": "平安银行", "exchange": "SZ"},
                {"stock_code": "830799", "short_name": "艾融软件", "exchange": "BJ"},
            ]
        )
    )
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket(), info))

    rows = provider_mod.ADataProvider().get_instruments("stock")

    assert info.calls == 1
    assert rows == [
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "code": "600519",
            "exchange": "SH",
            "region": "CN",
            "type": "stock",
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "code": "000001",
            "exchange": "SZ",
            "region": "CN",
            "type": "stock",
        },
        {
            "symbol": "830799.BJ",
            "name": "艾融软件",
            "code": "830799",
            "exchange": "BJ",
            "region": "CN",
            "type": "stock",
        },
    ]


def test_instruments_skip_invalid_rows_when_valid_ones_exist(monkeypatch):
    provider_mod = _provider_module()
    info = _FakeInfo(
        pd.DataFrame(
            [
                {"stock_code": None, "short_name": "坏数据", "exchange": "SH"},
                {"stock_code": "600519", "short_name": "贵州茅台", "exchange": "SH"},
                {"stock_code": "000001", "short_name": "平安银行", "exchange": None},
            ]
        )
    )
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket(), info))

    assert provider_mod.ADataProvider().get_instruments("stock") == [
        {
            "symbol": "600519.SH",
            "name": "贵州茅台",
            "code": "600519",
            "exchange": "SH",
            "region": "CN",
            "type": "stock",
        }
    ]


def test_instruments_empty_payload_fails_loudly(monkeypatch):
    provider_mod = _provider_module()
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket(), _FakeInfo(pd.DataFrame())))

    with pytest.raises(provider_mod.ADataProviderError, match="空响应"):
        provider_mod.ADataProvider().get_instruments("stock")


def test_instruments_schema_drift_fails_loudly(monkeypatch):
    provider_mod = _provider_module()
    info = _FakeInfo(pd.DataFrame([{"code": "600519", "short_name": "贵州茅台"}]))
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket(), info))

    with pytest.raises(provider_mod.ADataProviderError, match="stock_code"):
        provider_mod.ADataProvider().get_instruments("stock")


def test_instruments_all_invalid_rows_fail_loudly(monkeypatch):
    provider_mod = _provider_module()
    info = _FakeInfo(
        pd.DataFrame(
            [
                {"stock_code": None, "short_name": "坏数据", "exchange": "SH"},
                {"stock_code": "000001", "short_name": "平安银行", "exchange": None},
            ]
        )
    )
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket(), info))

    with pytest.raises(provider_mod.ADataProviderError, match="均无法识别"):
        provider_mod.ADataProvider().get_instruments("stock")


def test_unsupported_datasets_return_empty_without_error(monkeypatch):
    provider_mod = _provider_module()
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(_FakeMarket()))
    provider = provider_mod.ADataProvider()

    assert provider.get_adj_factors(["600519.SH"], None, None).is_empty()
    assert provider.get_minute(["600519.SH"], None, None).is_empty()
    assert provider.get_realtime().is_empty()
    assert provider.get_depth_batch(["600519.SH"]) == {}


def test_adata_failure_is_not_swallowed_as_success(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket(error=RuntimeError("AData unavailable"))
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))
    provider = provider_mod.ADataProvider()

    with pytest.raises(RuntimeError, match="AData unavailable"):
        provider.get_daily(["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7))

    with pytest.raises(RuntimeError, match="AData unavailable"):
        list(provider.iter_daily(["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)))


def test_adata_plugin_is_available(monkeypatch):
    from app.data_providers import custom

    custom.load_all()

    assert "adata" in custom.names()
    assert custom.provider_has_dataset("adata", "daily")
    assert not custom.provider_has_dataset("adata", "adj_factor")


def test_daily_missing_required_column_fails_loudly(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03").drop(columns=["close"])
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    with pytest.raises(provider_mod.ADataProviderError, match="close"):
        provider_mod.ADataProvider().get_daily(
            ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )


@pytest.mark.parametrize("payload", [{1, 2, 3}, {"bad": 1}], ids=["set", "dict"])
def test_daily_unparseable_non_empty_payload_fails_loudly(monkeypatch, payload):
    provider_mod = _provider_module()
    market = _FakeMarket({"600519": payload})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    with pytest.raises(provider_mod.ADataProviderError, match="无法解析"):
        provider_mod.ADataProvider().get_daily(
            ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )


def test_daily_all_null_close_fails_loudly(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03")
    raw["close"] = None
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    with pytest.raises(provider_mod.ADataProviderError, match="close"):
        provider_mod.ADataProvider().get_daily(
            ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )


def test_daily_unparseable_dates_fail_loudly(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03")
    raw["trade_time"] = None
    raw["trade_date"] = None
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    with pytest.raises(provider_mod.ADataProviderError, match="日期"):
        provider_mod.ADataProvider().get_daily(
            ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )


def test_daily_overrides_misattributed_symbol(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03")
    raw["symbol"] = "000001.SZ"
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    df = provider_mod.ADataProvider().get_daily(
        ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
    )

    assert df["symbol"].to_list() == ["600519.SH"]


def test_daily_empty_payload_returns_empty_and_reports_progress(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket({"600519": pd.DataFrame()})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))
    provider = provider_mod.ADataProvider()

    assert provider.get_daily(["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)).is_empty()

    progress: list[tuple[int, int]] = []
    frames = list(
        provider.iter_daily(
            ["600519.SH"],
            datetime(2023, 7, 3),
            datetime(2023, 7, 7),
            on_chunk_done=lambda cur, total: progress.append((cur, total)),
        )
    )

    assert frames == []
    assert progress == [(1, 1)]


def test_daily_rejects_non_stock_symbol_before_calling_sdk(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket()
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    with pytest.raises(provider_mod.ADataProviderError, match="510300"):
        provider_mod.ADataProvider().get_daily(
            ["510300.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )

    assert market.calls == []


def test_daily_rejects_exchange_mismatch_before_calling_sdk(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket()
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    # 000001.SH 是上证指数写法, 不能按深市股票 000001 静默取错资产。
    with pytest.raises(provider_mod.ADataProviderError, match=r"000001\.SH"):
        provider_mod.ADataProvider().get_daily(
            ["000001.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
        )

    assert market.calls == []


def test_daily_normalizes_bare_symbol_to_panel_suffix(monkeypatch):
    provider_mod = _provider_module()
    market = _FakeMarket({"600519": _daily_frame("600519", "2023-07-03")})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    df = provider_mod.ADataProvider().get_daily(
        ["600519"], datetime(2023, 7, 3), datetime(2023, 7, 7)
    )

    assert market.calls[0]["stock_code"] == "600519"
    assert df["symbol"].to_list() == ["600519.SH"]


def test_daily_accepts_trade_date_only_schema(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03").drop(columns=["trade_time"])
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    df = provider_mod.ADataProvider().get_daily(
        ["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7)
    )

    assert df["date"].to_list() == [date(2023, 7, 3)]


def test_daily_all_halted_rows_return_empty_without_error(monkeypatch):
    provider_mod = _provider_module()
    raw = _daily_frame("600519", "2023-07-03", volume=0)
    raw.loc[0, ["open", "high", "low", "close", "amount"]] = 0.0
    market = _FakeMarket({"600519": raw})
    monkeypatch.setattr(provider_mod, "adata", _fake_sdk(market))

    assert (
        provider_mod.ADataProvider()
        .get_daily(["600519.SH"], datetime(2023, 7, 3), datetime(2023, 7, 7))
        .is_empty()
    )
