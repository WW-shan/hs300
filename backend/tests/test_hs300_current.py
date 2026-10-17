"""HS300 当前成分获取的失败边界测试。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import polars as pl
import pytest

from app.hs300 import current


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._payload


def _member_frame(count: int) -> pl.DataFrame:
    return pl.DataFrame({
        "品种代码": [f"{i:06d}" for i in range(count)],
        "品种名称": [f"name-{i}" for i in range(count)],
    })


def test_fetch_current_members_rejects_partial_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """降级源返回 288 只时不能把残缺名单当成 HS300。"""
    monkeypatch.setattr(current, "_fetch_csindex_frame", lambda: (_ for _ in ()).throw(RuntimeError("csindex down")))
    monkeypatch.setattr(current, "_fetch_em_frame", lambda: _member_frame(288))

    with pytest.raises(current.CurrentMembersError, match="288"):
        current.fetch_current_members()


def test_em_fallback_paginates_with_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """东方财富降级必须分页取满 300, 且网络调用必须带显式 timeout。"""
    calls: list[tuple[str, float]] = []

    def fake_urlopen(url: str, timeout: float):
        calls.append((url, timeout))
        page = int(url.split("pn=", 1)[1].split("&", 1)[0])
        start = (page - 1) * 100
        rows = [
            {"f12": f"{i:06d}", "f14": f"name-{i}"}
            for i in range(start, min(start + 100, 300))
        ]
        return _FakeResponse(json.dumps({"data": {"total": 300, "diff": rows}}).encode())

    import akshare as ak

    monkeypatch.setattr(
        ak,
        "index_stock_cons",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应再调用无超时的 AkShare 降级接口")),
    )
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    frame = current._fetch_em_frame()
    assert frame.height == 300
    assert [call[1] for call in calls] == [current.HS300_HTTP_TIMEOUT] * 3


def test_csindex_fetch_uses_explicit_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """官方源网络调用必须有显式 timeout, 不能无限等待。"""
    import akshare as ak

    monkeypatch.setattr(
        ak,
        "index_stock_cons_csindex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应再调用无超时的 AkShare 官方接口")),
    )

    def fake_urlopen(_url: str, timeout: float):
        assert timeout == current.HS300_HTTP_TIMEOUT
        raise TimeoutError("slow csindex")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(TimeoutError, match="slow csindex"):
        current._fetch_csindex_frame()


def test_load_current_members_falls_back_to_cache_on_timeout(tmp_path: Path) -> None:
    cache = tmp_path / "current_members.json"
    cache.write_text(json.dumps({
        "as_of": "2026-09-30",
        "source": "csindex",
        "fetched_at": "2020-01-01T00:00:00+08:00",
        "count": 300,
        "members": [{"symbol": f"{i:06d}.SZ", "name": f"n{i}"} for i in range(300)],
    }), encoding="utf-8")

    def timeout():
        raise TimeoutError("network down")

    result = current.load_current_members(
        refresh=True,
        cache_path=cache,
        fetcher=timeout,
        background_refresh=False,
    )
    assert result.source == "csindex"
    assert len(result.members) == 300


def test_background_refresh_is_per_cache_path(tmp_path: Path) -> None:
    """不同缓存路径的后台刷新不能互相阻塞 (测试隔离/多数据目录)。"""
    cache_one = tmp_path / "one.json"
    cache_two = tmp_path / "two.json"
    payload = {
        "as_of": "2026-09-30",
        "source": "csindex",
        "fetched_at": "2020-01-01T00:00:00+08:00",
        "count": 1,
        "members": [{"symbol": "000001.SZ", "name": "old"}],
    }
    for path in (cache_one, cache_two):
        path.write_text(json.dumps(payload), encoding="utf-8")

    first_started = threading.Event()
    release_first = threading.Event()
    second_called = threading.Event()
    member = {"symbol": "600000.SH", "name": "new"}

    def first_fetcher() -> current.CurrentMembers:
        first_started.set()
        release_first.wait(timeout=5.0)
        return current.CurrentMembers(
            as_of="2026-10-16", source="csindex",
            fetched_at="2026-10-16T12:00:00+08:00", members=(member,),
        )

    def second_fetcher() -> current.CurrentMembers:
        second_called.set()
        return current.CurrentMembers(
            as_of="2026-10-16", source="csindex",
            fetched_at="2026-10-16T12:00:00+08:00", members=(member,),
        )

    try:
        assert current.load_current_members(cache_path=cache_one, fetcher=first_fetcher).as_of == "2026-09-30"
        assert first_started.wait(timeout=2.0)
        assert current.load_current_members(cache_path=cache_two, fetcher=second_fetcher).as_of == "2026-09-30"
        assert second_called.wait(timeout=2.0)
    finally:
        release_first.set()


def test_load_current_members_returns_stale_cache_immediately(tmp_path: Path) -> None:
    """过期缓存应先返回, 官方刷新在后台完成, 避免前端一直 loading。"""
    cache = tmp_path / "current_members.json"
    old_members = [{"symbol": f"{i:06d}.SZ", "name": f"old-{i}"} for i in range(300)]
    cache.write_text(json.dumps({
        "as_of": "2026-09-30",
        "source": "csindex",
        "fetched_at": "2020-01-01T00:00:00+08:00",
        "count": 300,
        "members": old_members,
    }), encoding="utf-8")

    fetched = threading.Event()
    new_members = tuple(
        {"symbol": f"{i:06d}.SZ", "name": f"new-{i}"}
        for i in range(300)
    )

    def fetcher() -> current.CurrentMembers:
        fetched.set()
        return current.CurrentMembers(
            as_of="2026-10-16",
            source="csindex",
            fetched_at="2026-10-16T12:00:00+08:00",
            members=new_members,
        )

    started = time.perf_counter()
    result = current.load_current_members(cache_path=cache, fetcher=fetcher)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5
    assert result.as_of == "2026-09-30"
    assert fetched.wait(timeout=2.0)

    deadline = time.time() + 2.0
    while time.time() < deadline:
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if stored["as_of"] == "2026-10-16":
            break
        time.sleep(0.02)
    assert json.loads(cache.read_text(encoding="utf-8"))["as_of"] == "2026-10-16"
