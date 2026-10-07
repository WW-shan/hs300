"""HS300 成分股 API — 快照浏览、PIT 成员查询与日线批量同步。

路由前缀: /api/hs300

端点:
  GET  /snapshots  月度快照日期列表
  GET  /members    指定查询日的成分股 (point-in-time)
  POST /sync       按区间成员并集同步日 K (默认 AkShare)

同步入口复用 kline_sync.sync_and_persist_daily_batch 与偏好路由机制,
不新增第二套数据源或写入链路。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import resolve_hs300_snapshot_dir, settings
from app.hs300.service import HS300MembershipError, HS300Service
from app.services import kline_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hs300", tags=["hs300"])

# 阻止多个 HS300 同步同时写入同一数据仓库。
_SYNC_LOCK = threading.Lock()
_SYNC_IN_PROGRESS = False


class SyncRequest(BaseModel):
    """HS300 日 K 同步入参; 区间缺省覆盖全部快照。"""

    start: date | None = None
    end: date | None = None
    provider: str = "akshare"


def _snapshot_root() -> Path:
    return resolve_hs300_snapshot_dir(settings.hs300_snapshot_dir)


def _service() -> HS300Service:
    return HS300Service(_snapshot_root())


def _daily_provider_available(name: str) -> bool:
    """Fail-closed: 仅允许真正声明了 daily 数据集的数据源。"""
    if name == "tickflow":
        return True
    from app.data_providers import custom as custom_sources

    return custom_sources.provider_has_dataset(name, "daily")


def _available_snapshots(service: HS300Service) -> list[date]:
    snapshots = service.snapshot_dates()
    if not snapshots:
        raise HTTPException(
            status_code=503,
            detail=f"未找到 CSI300 成分股快照: {_snapshot_root()}",
        )
    return snapshots


def _refresh_enriched_for_symbols(repo, symbols: list[str]) -> None:
    """Publish synced raw daily bars to the enriched reads used by panel pages."""
    from app.indicators.pipeline import run_pipeline

    run_pipeline(data_dir=repo.store.data_dir, symbols=symbols)

    from app.api.data import invalidate_storage_cache
    from app.jobs.daily_pipeline import _refresh_single_view

    _refresh_single_view(repo, "kline_enriched")
    repo.clear_cache()
    repo.refresh_cache()
    invalidate_storage_cache()


@router.get("/snapshots")
def list_snapshots() -> dict:
    """返回可用月度快照日期 (升序)。"""
    service = _service()
    snapshots = _available_snapshots(service)
    return {
        "snapshots": [day.isoformat() for day in snapshots],
        "earliest": snapshots[0].isoformat(),
        "latest": snapshots[-1].isoformat(),
    }


@router.get("/members")
def get_members(
    as_of: Annotated[date, Query(description="查询日 YYYY-MM-DD, 取不晚于该日的最近一份快照")],
) -> dict:
    """返回 PIT 语义下的 HS300 成分股 (最新快照在 on/before 查询日生效)。"""
    service = _service()
    _available_snapshots(service)
    try:
        members = service.members(as_of)
    except HS300MembershipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "as_of": as_of.isoformat(),
        "snapshot_date": members["snapshot_date"][0].isoformat(),
        "members": members.select("symbol", "name").to_dicts(),
    }


@router.get("/members-between")
def get_members_between(
    start: Annotated[date, Query(description="起始日 YYYY-MM-DD")],
    end: Annotated[date, Query(description="结束日 YYYY-MM-DD")],
) -> dict:
    """返回区间内所有快照成分的并集 (供回测加载数据; PIT 过滤仍由服务端逐日执行)。"""
    service = _service()
    snapshots = _available_snapshots(service)
    if start < snapshots[0]:
        raise HTTPException(
            status_code=422,
            detail=f"起始日 {start.isoformat()} 早于首个快照 {snapshots[0].isoformat()}",
        )
    if start > end:
        raise HTTPException(
            status_code=422,
            detail=f"起始日 {start.isoformat()} 晚于结束日 {end.isoformat()}",
        )
    try:
        frame = service.members_between(start, end)
    except HS300MembershipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    latest_name = (
        frame.sort(["symbol", "snapshot_date"])
        .unique(subset=["symbol"], keep="last")
        .sort("symbol")
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "snapshot_dates": sorted(d.isoformat() for d in frame["snapshot_date"].unique()),
        "members": latest_name.select("symbol", "name").to_dicts(),
    }


@router.post("/sync")
def sync_daily(request: Request, payload: SyncRequest | None = None) -> dict:
    """按区间成分股并集同步日 K, 数据源覆盖仅作用于本次调用。"""
    body = payload or SyncRequest()
    service = _service()
    snapshots = _available_snapshots(service)

    start = body.start or snapshots[0]
    end = body.end or snapshots[-1]
    if start < snapshots[0]:
        raise HTTPException(
            status_code=422,
            detail=f"同步起始日 {start.isoformat()} 早于首个快照 {snapshots[0].isoformat()}",
        )
    if start > end:
        raise HTTPException(
            status_code=422,
            detail=f"同步起始日 {start.isoformat()} 晚于结束日 {end.isoformat()}",
        )

    provider = str(body.provider or "akshare").strip().lower()
    if not provider or not _daily_provider_available(provider):
        raise HTTPException(
            status_code=422,
            detail=f"数据源 {body.provider!r} 未提供日K数据集",
        )

    try:
        membership = service.members_between(start, end)
    except HS300MembershipError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    symbols = membership["symbol"].unique(maintain_order=True).to_list()

    global _SYNC_IN_PROGRESS
    with _SYNC_LOCK:
        if _SYNC_IN_PROGRESS:
            raise HTTPException(status_code=409, detail="HS300 日K同步正在执行, 请稍后重试")
        _SYNC_IN_PROGRESS = True
    try:
        repo = request.app.state.repo
        capset = request.app.state.capabilities
        zero_row_symbols: list[str] = []
        rows = kline_sync.sync_and_persist_daily_batch(
            symbols,
            repo,
            capset,
            start_date=datetime.combine(start, time.min),
            end_date=datetime.combine(end, time.max),
            provider_name=provider,
            zero_row_out=zero_row_symbols,
        )
        if rows > 0:
            _refresh_enriched_for_symbols(repo, symbols)
    finally:
        with _SYNC_LOCK:
            _SYNC_IN_PROGRESS = False

    if rows == 0:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "数据源未返回可写入的日 K 数据",
                "provider": provider,
                "symbols": len(symbols),
                "rows": rows,
                "zero_row_symbols": zero_row_symbols,
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
        )

    logger.info(
        "HS300 日K同步完成: provider=%s symbols=%d rows=%d range=%s..%s",
        provider,
        len(symbols),
        rows,
        start,
        end,
    )
    return {
        "ok": True,
        "provider": provider,
        "symbols": len(symbols),
        "rows": rows,
        "zero_row_symbols": zero_row_symbols,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
