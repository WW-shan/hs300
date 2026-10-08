"""HS300 动态筛选 API 测试 — 官方当前成分查询。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import hs300 as hs300_api
from app.hs300.current import CurrentMembers, CurrentMembersError


def _members(symbols: list[str]) -> CurrentMembers:
    return CurrentMembers(
        as_of="2026-09-30",
        source="csindex",
        fetched_at="2026-10-08T12:00:00+08:00",
        members=tuple(
            {"symbol": symbol, "name": f"name-{index}"}
            for index, symbol in enumerate(symbols)
        ),
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        hs300_api,
        "load_current_members",
        lambda **_kwargs: _members(["600519.SH", "000001.SZ", "300750.SZ"]),
    )
    app = FastAPI()
    app.include_router(hs300_api.router)
    return TestClient(app)


def test_current_returns_official_members(client) -> None:
    resp = client.get("/api/hs300/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["as_of"] == "2026-09-30"
    assert body["source"] == "csindex"
    assert body["count"] == 3
    assert [m["symbol"] for m in body["members"]] == ["600519.SH", "000001.SZ", "300750.SZ"]


def test_current_forwards_refresh_flag(client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def _loader(**kwargs):
        calls.append(kwargs)
        return _members(["600519.SH"])

    monkeypatch.setattr(hs300_api, "load_current_members", _loader)
    resp = client.get("/api/hs300/current?refresh=true")
    assert resp.status_code == 200
    assert calls == [{"refresh": True}]


def test_current_fail_closed_without_snapshot_fallback(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kwargs):
        raise CurrentMembersError("官方接口不可用")

    monkeypatch.setattr(hs300_api, "load_current_members", _boom)
    resp = client.get("/api/hs300/current")
    assert resp.status_code == 503
    assert "官方接口不可用" in resp.json()["detail"]


def test_sync_endpoint_is_gone(client) -> None:
    """行情同步统一走全市场日K管线, 不再有按名单的同步入口。"""
    assert client.post("/api/hs300/sync", json={"provider": "akshare"}).status_code == 404
