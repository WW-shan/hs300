"""_write_lock 锁区间瘦身的回归测试。

背景: polars 并发执行存在死锁风险 (app.polars_guard), 若 polars 读/合并/排序
悬死在 _write_lock 内, 全局写锁被永久持有 → 所有写路径排队冻结 (2026-09-07
线上全站冻结事故的放大器)。乐观并发模式把重活移到锁外, 此处验证:
- 并发 upsert 不丢行 (乐观重试的正确性);
- 合并计算期间 _write_lock 可被其他线程获取 (重活确实不在锁内)。
"""
from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

import polars as pl

from app.tickflow.repository import DataStore, KlineRepository


def _frame(symbols: list[str], dt: date = date(2026, 9, 7)) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame({
        "symbol": symbols,
        "date": [dt] * n,
        "close": [10.0 + i for i in range(n)],
    })


def test_concurrent_upserts_do_not_lose_rows(tmp_path: Path) -> None:
    repo = KlineRepository(DataStore(tmp_path))

    groups = [[f"{i:03d}{j:04d}.SZ" for j in range(8)] for i in range(6)]
    errors: list[BaseException] = []

    def worker(symbols: list[str]) -> None:
        try:
            for _ in range(3):  # 每线程多轮写, 提高乐观重试路径命中
                repo.merge_live_daily_asset("stock", _frame(symbols))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(g,), daemon=True) for g in groups]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert all(not t.is_alive() for t in threads)

    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    final = pl.read_parquet(out)
    assert final["symbol"].n_unique() == sum(len(g) for g in groups)  # 无一丢失
    assert final["symbol"].to_list() == sorted(final["symbol"].to_list())


def test_heavy_merge_runs_outside_write_lock(tmp_path: Path, monkeypatch) -> None:
    repo = KlineRepository(DataStore(tmp_path))
    # 预置旧分区内容, 让 upsert 走「读旧 + concat 合并」路径。
    repo.merge_live_daily_asset("stock", _frame(["000001.SZ"]))

    merge_entered = threading.Event()
    original_concat = pl.concat

    def slow_concat(*args, **kwargs):
        merge_entered.set()
        import time

        time.sleep(0.4)  # 模拟重合并耗时; 期间 _write_lock 必须是空闲的
        return original_concat(*args, **kwargs)

    monkeypatch.setattr(pl, "concat", slow_concat)

    done = threading.Event()

    def upsert() -> None:
        repo.merge_live_daily_asset("stock", _frame(["000002.SZ"]))
        done.set()

    t = threading.Thread(target=upsert, daemon=True)
    t.start()
    assert merge_entered.wait(timeout=5), "合并路径未被触发"

    acquired = repo._write_lock.acquire(timeout=1.0)
    assert acquired, "合并计算期间 _write_lock 被占用 — 重活仍在锁内"
    repo._write_lock.release()

    assert done.wait(timeout=5)
    t.join(timeout=5)
    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    assert pl.read_parquet(out)["symbol"].to_list() == ["000001.SZ", "000002.SZ"]


def test_upsert_retries_when_partition_changes_during_read(tmp_path: Path, monkeypatch) -> None:
    """读旧快照期间发生的并发写入不能被后续合并覆盖。"""
    repo = KlineRepository(DataStore(tmp_path))
    repo.merge_live_daily_asset("stock", _frame(["000001.SZ"]))

    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    original_read = pl.read_parquet
    first_read_entered = threading.Event()
    release_first_read = threading.Event()
    read_count = 0

    def delayed_read(source, *args, **kwargs):
        nonlocal read_count
        read_count += 1
        frame = original_read(source, *args, **kwargs)
        if read_count == 1:
            first_read_entered.set()
            assert release_first_read.wait(timeout=5), "首次读取未放行"
        return frame

    monkeypatch.setattr(pl, "read_parquet", delayed_read)

    errors: list[BaseException] = []

    def upsert() -> None:
        try:
            repo.merge_live_daily_asset("stock", _frame(["000003.SZ"]))
        except BaseException as exc:  # noqa: BLE001 - 线程异常必须带出断言
            errors.append(exc)

    thread = threading.Thread(target=upsert, daemon=True)
    thread.start()
    assert first_read_entered.wait(timeout=5), "首次读取未发生"

    # 首次读取已经取得旧快照，但尚未返回；此时另一个写者先提交一行。
    repo.merge_live_daily_asset("stock", _frame(["000002.SZ"]))
    release_first_read.set()
    thread.join(timeout=5)

    assert not errors, errors
    assert not thread.is_alive()
    assert pl.read_parquet(out)["symbol"].to_list() == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    ]


def test_upsert_pins_partition_before_parquet_read(tmp_path: Path, monkeypatch) -> None:
    """路径式读取跨 os.replace 会混读两个 inode，必须固定文件快照。"""
    repo = KlineRepository(DataStore(tmp_path))
    repo.merge_live_daily_asset("stock", _frame(["000001.SZ"]))

    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    original_read = pl.read_parquet

    def pinned_read(source, *args, **kwargs):
        assert not isinstance(source, (str, Path)), (
            "upsert 必须从已打开的 parquet 文件句柄读取，不能用可能被 os.replace 切换的路径"
        )
        return original_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", pinned_read)
    repo.merge_live_daily_asset("stock", _frame(["000002.SZ"]))

    assert original_read(out)["symbol"].to_list() == ["000001.SZ", "000002.SZ"]


def test_upsert_retry_fallback_pins_partition_before_parquet_read(
    tmp_path: Path, monkeypatch
) -> None:
    """乐观重试耗尽后的锁内回退也必须固定读取快照。"""
    repo = KlineRepository(DataStore(tmp_path))
    repo.merge_live_daily_asset("stock", _frame(["000001.SZ"]))

    out = tmp_path / "kline_daily" / "date=2026-09-07" / "part.parquet"
    original_read = pl.read_parquet

    def pinned_read(source, *args, **kwargs):
        assert not isinstance(source, (str, Path)), "回退路径不能按路径读取 parquet"
        return original_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", pinned_read)
    repo._optimistic_upsert_partition(out, _frame(["000002.SZ"]), None, retries=0)

    assert original_read(out)["symbol"].to_list() == ["000001.SZ", "000002.SZ"]


def test_flush_enriched_pins_partition_before_parquet_read(tmp_path: Path, monkeypatch) -> None:
    """盘后/实时覆写前的旧内容探测也不能按可变路径读取。"""
    repo = KlineRepository(DataStore(tmp_path))
    out = tmp_path / "kline_index_enriched" / "date=2026-09-07" / "part.parquet"
    out.parent.mkdir(parents=True)
    repo._atomic_write_parquet(_frame(["000001.SH"]), out)

    original_read = pl.read_parquet

    def pinned_read(source, *args, **kwargs):
        assert not isinstance(source, (str, Path)), "覆写路径不能按路径读取 parquet"
        return original_read(source, *args, **kwargs)

    monkeypatch.setattr(pl, "read_parquet", pinned_read)
    repo.flush_live_enriched_asset("index", _frame(["000002.SH"]))

    assert original_read(out)["symbol"].to_list() == ["000002.SH"]
