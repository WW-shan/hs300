"""HS300 动态筛选 API — 官方当前成分查询。

路由前缀: /api/hs300

端点:
  GET  /current  官方最新沪深300成分 (中证指数 -> 东方财富降级, 6h 缓存)

只有"名单"接口: HS300 是可开关的动态筛选器, 不维护固定 300 池, 也不提供
按名单同步日K (行情由系统日K管线按全市场统一同步, 名单与数据解耦)。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.hs300.current import CurrentMembersError, load_current_members

router = APIRouter(prefix="/api/hs300", tags=["hs300"])


def _current_members_or_503(refresh: bool = False):
    try:
        return load_current_members(refresh=refresh)
    except CurrentMembersError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/current")
def get_current(
    refresh: bool = Query(False, description="true 时忽略 6h 缓存, 强制拉取官方接口"),
) -> dict:
    """返回官方最新沪深300成分; 失败时 fail-closed, 不回退历史快照。"""
    members = _current_members_or_503(refresh=refresh)
    return members.to_dict()
