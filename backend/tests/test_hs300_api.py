"""HS300 API 测试 — 快照/成员查询与同步的 provider 路由、范围校验与恢复。"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import hs300 as hs300_api
from app.services import preferences


def _snapshot(root: Path, year: int, month: int, symbols: list[str]) -> None:
    path = root / f"{year:04d}" / f"{month:02d}" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"Symbol": symbols, "Name": [f"name-{i}" for i in range(len(symbols))]}).write_csv(
        path
    )


class _FakeRepo:
    def __init__(self, data_dir: Path) -> None:
        self.store = SimpleNamespace(data_dir=data_dir)
        self.cache_calls: list[str] = []

    def clear_cache(self) -> None:
        self.cache_calls.append("clear")

    def refresh_cache(self) -> None:
        self.cache_calls.append("refresh")


class _FakeCapabilities:
    pass


class _SyncRecorder:
    def __init__(
        self,
        result: int = 42,
        error: Exception | None = None,
        zero_row_symbols: list[str] | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self.result = result
        self.error = error
        self.zero_row_symbols = zero_row_symbols or []

    def __call__(self, symbols, repo, capset, **kwargs):
        self.calls.append(
            {
                "symbols": list(symbols),
                "repo": repo,
                "capset": capset,
                "kwargs": kwargs,
                "provider_during_call": preferences.get_daily_data_provider(),
                "sync_lock_held": hs300_api._SYNC_LOCK.locked(),
            }
        )
        zero_row_out = kwargs.get("zero_row_out")
        if zero_row_out is not None:
            zero_row_out.extend(self.zero_row_symbols)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def snapshot_root(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    _snapshot(root, 2023, 7, ["600519.SS", "000001.SZ"])
    _snapshot(root, 2023, 8, ["600519.SS", "300750.SZ"])
    return root


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot_root: Path):
    monkeypatch.setattr(hs300_api.settings, "hs300_snapshot_dir", snapshot_root)
    monkeypatch.setattr(
        hs300_api, "_daily_provider_available", lambda name: name in {"adata", "tickflow"}
    )
    monkeypatch.setattr(preferences, "_path", lambda: tmp_path / "preferences.json")
    preferences._invalidate_cache()
    repo = _FakeRepo(tmp_path / "store")
    capset = _FakeCapabilities()
    app = FastAPI()
    app.include_router(hs300_api.router)
    app.state.repo = repo
    app.state.capabilities = capset
    monkeypatch.setattr("app.indicators.pipeline.run_pipeline", lambda **_kwargs: 0)
    monkeypatch.setattr("app.jobs.daily_pipeline._refresh_single_view", lambda *_args: None)
    monkeypatch.setattr("app.api.data.invalidate_storage_cache", lambda: None)
    yield TestClient(app), repo, capset
    preferences._invalidate_cache()


def test_snapshots_list_all_months(client) -> None:
    http, _, _ = client

    resp = http.get("/api/hs300/snapshots")

    assert resp.status_code == 200
    assert resp.json() == {
        "snapshots": ["2023-07-01", "2023-08-01"],
        "earliest": "2023-07-01",
        "latest": "2023-08-01",
    }


def test_members_use_latest_snapshot_and_normalize_sh(client) -> None:
    http, _, _ = client

    resp = http.get("/api/hs300/members", params={"as_of": "2023-07-31"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2023-07-31"
    assert body["snapshot_date"] == "2023-07-01"
    assert body["members"] == [
        {"symbol": "600519.SH", "name": "name-0"},
        {"symbol": "000001.SZ", "name": "name-1"},
    ]


def test_members_before_first_snapshot_return_422(client) -> None:
    http, _, _ = client

    resp = http.get("/api/hs300/members", params={"as_of": "2023-06-30"})

    assert resp.status_code == 422
    assert "2023-07-01" in resp.json()["detail"]


def test_sync_uses_membership_union_without_mutating_global_provider(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, repo, capset = client
    preferences.save({"daily_data_provider": "tickflow"})
    recorder = _SyncRecorder(result=17)
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"provider": "adata"})

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "provider": "adata",
        "symbols": 3,
        "rows": 17,
        "zero_row_symbols": [],
        "start": "2023-07-01",
        "end": "2023-08-01",
    }
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["symbols"] == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert call["repo"] is repo
    assert call["capset"] is capset
    assert call["kwargs"]["start_date"] == datetime.combine(date(2023, 7, 1), time.min)
    assert call["kwargs"]["end_date"] == datetime.combine(date(2023, 8, 1), time.max)
    assert call["kwargs"]["provider_name"] == "adata"
    assert call["provider_during_call"] == "tickflow"
    assert call["sync_lock_held"] is False
    assert preferences.get_daily_data_provider() == "tickflow"


def test_sync_recomputes_enriched_for_synced_members(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, repo, _ = client
    recorder = _SyncRecorder(result=17)
    pipeline_calls: list[dict] = []
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    monkeypatch.setattr(
        "app.indicators.pipeline.run_pipeline",
        lambda **kwargs: pipeline_calls.append(kwargs) or 17,
    )

    response = http.post("/api/hs300/sync", json={"provider": "tickflow"})

    assert response.status_code == 200
    assert pipeline_calls == [{"data_dir": repo.store.data_dir, "symbols": [
        "600519.SH", "000001.SZ", "300750.SZ",
    ]}]
    assert repo.cache_calls == ["clear", "refresh"]


def test_sync_explicit_range_selects_covering_snapshot(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"start": "2023-08-05", "end": "2023-08-20"})

    assert resp.status_code == 200
    assert recorder.calls[0]["symbols"] == ["600519.SH", "300750.SZ"]
    assert resp.json()["start"] == "2023-08-05"
    assert resp.json()["end"] == "2023-08-20"


def test_sync_does_not_mutate_global_provider_when_upstream_raises(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, _ = client
    preferences.save({"daily_data_provider": "tickflow"})
    recorder = _SyncRecorder(error=RuntimeError("adata boom"))
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    with pytest.raises(RuntimeError, match="adata boom"):
        http.post("/api/hs300/sync", json={"provider": "adata"})

    assert preferences.get_daily_data_provider() == "tickflow"
    assert hs300_api._SYNC_IN_PROGRESS is False


def test_sync_rejects_concurrent_request_without_calling_provider(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)
    monkeypatch.setattr(hs300_api, "_SYNC_IN_PROGRESS", True)

    response = http.post("/api/hs300/sync", json={"provider": "adata"})

    assert response.status_code == 409
    assert recorder.calls == []


def test_sync_returns_zero_row_symbols_for_partial_results(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, _ = client
    recorder = _SyncRecorder(result=9, zero_row_symbols=["300750.SZ"])
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"provider": "adata"})

    assert resp.status_code == 200
    assert resp.json()["rows"] == 9
    assert resp.json()["zero_row_symbols"] == ["300750.SZ"]


def test_sync_fails_when_provider_returns_no_rows(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder(result=0, zero_row_symbols=["600519.SH", "000001.SZ"])
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"provider": "adata"})

    assert resp.status_code == 502
    assert resp.json()["detail"]["zero_row_symbols"] == ["600519.SH", "000001.SZ"]


def test_sync_rejects_range_before_first_snapshot(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"start": "2023-06-01", "end": "2023-07-31"})

    assert resp.status_code == 422
    assert recorder.calls == []


def test_sync_rejects_unknown_daily_provider(client, monkeypatch: pytest.MonkeyPatch) -> None:
    http, _, _ = client
    recorder = _SyncRecorder()
    monkeypatch.setattr(hs300_api.kline_sync, "sync_and_persist_daily_batch", recorder)

    resp = http.post("/api/hs300/sync", json={"provider": "missing"})

    assert resp.status_code == 422
    assert recorder.calls == []


def test_members_between_returns_snapshot_union(client) -> None:
    http, _, _ = client

    resp = http.get(
        "/api/hs300/members-between",
        params={"start": "2023-07-15", "end": "2023-08-15"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == "2023-07-15"
    assert body["end"] == "2023-08-15"
    assert body["snapshot_dates"] == ["2023-07-01", "2023-08-01"]
    assert [member["symbol"] for member in body["members"]] == [
        "000001.SZ",
        "300750.SZ",
        "600519.SH",
    ]


def test_members_between_before_first_snapshot_returns_422(client) -> None:
    http, _, _ = client

    resp = http.get(
        "/api/hs300/members-between",
        params={"start": "2023-06-15", "end": "2023-07-15"},
    )

    assert resp.status_code == 422
    assert "2023-07-01" in resp.json()["detail"]


def test_snapshot_root_falls_back_when_env_is_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path as _Path

    from app.config import hs300_snapshot_default_dir

    monkeypatch.setattr(hs300_api.settings, "hs300_snapshot_dir", _Path(""))

    assert hs300_api._snapshot_root() == hs300_snapshot_default_dir()


def test_sync_end_to_end_writes_normalized_rows_through_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_root: Path,
) -> None:
    """冒烟: /sync 走真实 ADataProvider + KlineRepository 落盘 (SDK 用假数据, 无网络)。"""
    import pandas as pd

    from app.config import settings as app_settings
    from app.data_providers import custom
    from app.plugins.adata import provider as adata_provider
    from app.tickflow.repository import DataStore, KlineRepository

    monkeypatch.setattr(preferences, "_path", lambda: tmp_path / "preferences.json")
    preferences._invalidate_cache()
    monkeypatch.setattr(app_settings, "data_dir", tmp_path)
    monkeypatch.setattr(hs300_api.settings, "hs300_snapshot_dir", snapshot_root)
    custom.load_all()  # 注册内置 adata 插件 (availability 只 import SDK, 不联网)

    def _frame(stock_code: str) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "stock_code": stock_code,
                    "trade_time": "2023-07-03 00:00:00",
                    "trade_date": "2023-07-03",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 3_000_000,
                    "amount": 31_500_000.0,
                    "change_pct": 0.5,
                    "change": 0.05,
                }
            ]
        )

    class _FakeMarket:
        def get_market(self, **kwargs):
            return _frame(kwargs["stock_code"])

    monkeypatch.setattr(
        adata_provider,
        "adata",
        SimpleNamespace(
            stock=SimpleNamespace(
                market=_FakeMarket(),
                info=SimpleNamespace(all_code=lambda: pd.DataFrame()),
            )
        ),
    )

    repo = KlineRepository(DataStore(tmp_path))
    app = FastAPI()
    app.include_router(hs300_api.router)
    app.state.repo = repo
    app.state.capabilities = _FakeCapabilities()
    http = TestClient(app)

    try:
        resp = http.post("/api/hs300/sync", json={"provider": "adata"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "adata"
        assert body["symbols"] == 3
        assert body["rows"] == 3
        stored = pl.read_parquet(
            repo.store.data_dir / "kline_daily" / "date=2023-07-03" / "part.parquet"
        )
        assert sorted(stored["symbol"].to_list()) == ["000001.SZ", "300750.SZ", "600519.SH"]
        assert stored["close"].to_list() == [10.5, 10.5, 10.5]
        assert stored["volume"].to_list() == [30_000.0, 30_000.0, 30_000.0]  # 股 → 手

        # AData's @handler_null may turn upstream exceptions into empty frames;
        # the API must surface that as failure instead of a successful zero-row sync.
        monkeypatch.setattr(
            adata_provider,
            "adata",
            SimpleNamespace(
                stock=SimpleNamespace(
                    market=SimpleNamespace(get_market=lambda **kwargs: pd.DataFrame()),
                    info=SimpleNamespace(all_code=lambda: pd.DataFrame()),
                )
            ),
        )
        empty_resp = http.post("/api/hs300/sync", json={"provider": "adata"})
        assert empty_resp.status_code == 502
        assert empty_resp.json()["detail"]["rows"] == 0
        assert empty_resp.json()["detail"]["zero_row_symbols"] == [
            "600519.SH",
            "000001.SZ",
            "300750.SZ",
        ]
    finally:
        preferences._invalidate_cache()
