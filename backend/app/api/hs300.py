"""HS300 动态筛选 API — 官方当前成分查询与按当前名单同步日 K。

路由前缀: /api/hs300

端点:
  GET  /current  官方最新沪深300成分 (中证指数 -> 东方财富降级, 6h 缓存)
  POST /sync     按当前成分名单同步日 K (默认 AkShare)

不再提供月度快照浏览/区间并集接口: HS300 只作为动态筛选器, 不维护固定 300 池。
同步入口复用 kline_sync.sync_and_persist_daily_batch 与偏好路由机制, 不新增
第二套数据源或写入链路。
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.hs300.current import CurrentMembersError, load_current_members
from app.services import kline_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hs300", tags=["hs300"])

# 阻止多个 HS300 同步同时写入同一数据仓库。
_SYNC_LOCK = threading.Lock()
_SYNC_IN_PROGRESS = False


class SyncRequest(BaseModel):
    """HS300 日 K 同步入参; 区间缺省为最近三年。"""

    start: date | None = None
    end: date | None = None
    provider: str = "akshare"


def _daily_provider_available(name: str) -> bool:
    """Fail-closed: 仅允许真正声明了 daily 数据集的数据源。"""
    if name == "tickflow":
        return True
    from app.data_providers import custom as custom_sources

    return custom_sources.provider_has_dataset(name, "daily")


def _current_members_or_503(refresh: bool = False):
    try:
        return load_current_members(refresh=refresh)
    except CurrentMembersError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


@router.get("/current")
def get_current(
    refresh: bool = Query(False, description="true 时忽略 6h 缓存, 强制拉取官方接口"),
) -> dict:
    """返回官方最新沪深300成分; 失败时 fail-closed, 不回退历史快照。"""
    members = _current_members_or_503(refresh=refresh)
    return members.to_dict()


@router.post("/sync")
def sync_daily(request: Request, payload: SyncRequest | None = None) -> dict:
    """按当前成分名单同步日 K, 数据源覆盖仅作用于本次调用。"""
    body = payload or SyncRequest()
    members = _current_members_or_503()
    symbols = members.symbols
    if not symbols:
        raise HTTPException(status_code=503, detail="HS300 当前成分为空, 拒绝同步")

    end = body.end or date.today()
    start = body.start or (end - timedelta(days=365 * 3))
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
        "as_of": members.as_of,
        "source": members.source,
    }
