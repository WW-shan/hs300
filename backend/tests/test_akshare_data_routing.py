from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.services import index_sync, kline_sync


class _Provider:
    def __init__(self):
        self.calls = []

    def supports_asset_type(self, dataset: str, asset_type: str) -> bool:
        return dataset == "daily" and asset_type in {"index", "etf"}

    def get_daily(self, symbols, start_time, end_time, asset_type="stock", on_chunk_done=None):
        self.calls.append((symbols, start_time, end_time, asset_type))
        return pl.DataFrame({
            "symbol": symbols,
            "date": [datetime(2026, 9, 30).date()] * len(symbols),
            "open": [1.0] * len(symbols),
            "high": [1.0] * len(symbols),
            "low": [1.0] * len(symbols),
            "close": [1.0] * len(symbols),
            "volume": [10] * len(symbols),
            "amount": [None] * len(symbols),
        })


def _register_akshare(monkeypatch, provider):
    from app.data_providers import custom

    monkeypatch.setattr(
        custom,
        "provider_has_dataset",
        lambda name, dataset: name == "akshare" and dataset == "daily",
    )
    monkeypatch.setattr(custom, "get_provider", lambda name: provider)


def test_sync_daily_batch_routes_explicit_custom_asset_type(monkeypatch):
    provider = _Provider()
    _register_akshare(monkeypatch, provider)
    monkeypatch.setattr(kline_sync, "get_client", lambda: pytest.fail("must not use TickFlow"))

    frame = kline_sync.sync_daily_batch(
        ["000300.SH"],
        start_time=datetime(2026, 9, 30),
        end_time=datetime(2026, 9, 30),
        asset_type="index",
        provider_name="akshare",
    )

    assert provider.calls[0][0] == ["000300.SH"]
    assert provider.calls[0][3] == "index"
    assert frame["symbol"].to_list() == ["000300.SH"]


@pytest.mark.parametrize(
    ("sync_fn", "asset_type", "symbol"),
    [
        ("sync_and_persist_index_daily", "index", "000300.SH"),
        ("sync_and_persist_etf_daily", "etf", "510300.SH"),
    ],
)
def test_index_sync_uses_custom_capability_without_tickflow_tier(
    monkeypatch, tmp_path, sync_fn, asset_type, symbol
):
    provider = _Provider()
    _register_akshare(monkeypatch, provider)
    monkeypatch.setattr(index_sync.preferences, "get_daily_data_provider", lambda: "akshare")
    monkeypatch.setattr(
        index_sync,
        "resolve_limit",
        lambda *args, **kwargs: SimpleNamespace(batch=100, rpm=None),
    )
    monkeypatch.setattr(index_sync, "min_batch", lambda configured, limit: 100)
    monkeypatch.setattr(index_sync, "sleep_between_batches", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        index_sync.kline_sync,
        "sync_daily_batch",
        lambda symbols, **kwargs: capture(symbols, kwargs),
    )
    captured = []

    def capture(symbols, kwargs):
        captured.append((list(symbols), kwargs))
        return pl.DataFrame()

    class _Capset:
        @staticmethod
        def has(_cap):
            return False

    class _Repo:
        store = SimpleNamespace(data_dir=tmp_path)

        @staticmethod
        def refresh_index_views():
            return None

    sync = getattr(index_sync, sync_fn)
    sync(
        _Repo(),
        _Capset(),
        start_date=datetime(2026, 9, 30),
        end_date=datetime(2026, 9, 30),
        symbols_override=[symbol],
    )

    assert captured[0][0] == [symbol]
    assert captured[0][1]["asset_type"] == asset_type


def test_index_and_etf_instrument_sync_use_custom_daily_provider(monkeypatch):
    class _InstrumentProvider(_Provider):
        def get_instruments(self, asset_type):
            code = "000300.SH" if asset_type == "index" else "510300.SH"
            return [{"symbol": code, "name": asset_type}]

    provider = _InstrumentProvider()
    _register_akshare(monkeypatch, provider)
    monkeypatch.setattr(index_sync.preferences, "get_daily_data_provider", lambda: "akshare")
    monkeypatch.setattr(
        index_sync,
        "get_client",
        lambda: pytest.fail("must not use TickFlow instruments"),
    )

    indexes = index_sync._fetch_instruments_by_type("index", "index")
    etfs = index_sync._fetch_instruments_by_type("etf", "etf")

    assert indexes.select("symbol", "asset_type").to_dicts() == [
        {"symbol": "000300.SH", "asset_type": "index"}
    ]
    assert etfs.select("symbol", "asset_type").to_dicts() == [
        {"symbol": "510300.SH", "asset_type": "etf"}
    ]
