"""行业分类挂载 — 供评分中性化 (R17) 使用。

数据源: 内置扩展表 `ext_hy_ths` (同花顺行业, snapshot 模式, 由「概念/行业」页手动获取)。
只取一级行业 (「所属同花顺行业」按 `-` 拆分后的第一段)。

纪律:
  - 快照语义: 用"当前"行业分类回填历史 (与 HS300 当前成分回填历史同类偏差, 调用方需在结论里标注)。
  - fail-closed: 扩展表缺失/字段缺失/为空/无 symbol 列 → 抛 ValueError, 不静默退化为"未中性化"
    (R12 的静默丢弃坑同族)。
  - mtime 缓存: 同一进程内文件未变时复用解析结果, 避免每个回测重复读盘。
"""
from __future__ import annotations

import threading
from pathlib import Path

import polars as pl

INDUSTRY_COLUMN = "industry_l1"
_EXT_ID = "ext_hy_ths"
_EXT_FIELD = "所属同花顺行业"

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, pl.DataFrame]] = {}


def _ext_path(data_dir: Path | str | None) -> Path:
    if data_dir is None:
        raise ValueError("行业中性化需要本地数据目录, 当前不可用")
    return Path(data_dir) / "ext_data" / _EXT_ID / "part.parquet"


def load_industry_l1(data_dir: Path | str | None) -> pl.DataFrame:
    """返回 [symbol, industry_l1] 映射表; 缺失/为空时 fail-closed。"""
    path = _ext_path(data_dir)
    if not path.exists():
        raise ValueError(
            f"行业中性化缺少扩展表 {_EXT_ID} ({path}); 请先在「概念/行业」页获取行业数据"
        )
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:  # pragma: no cover - 权限/IO 异常
        raise ValueError(f"行业扩展表不可读: {exc}") from exc

    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]

    df = pl.read_parquet(path)
    missing = [name for name in ("symbol", _EXT_FIELD) if name not in df.columns]
    if missing:
        raise ValueError(f"行业扩展表缺少字段: {missing}")
    out = (
        df.select(
            [
                pl.col("symbol").cast(pl.Utf8),
                pl.col(_EXT_FIELD)
                .cast(pl.Utf8)
                .str.split("-")
                .list.first()
                .alias(INDUSTRY_COLUMN),
            ]
        )
        .drop_nulls()
        .filter(pl.col(INDUSTRY_COLUMN) != "")
        .unique(subset=["symbol"], keep="last")
    )
    if out.is_empty():
        raise ValueError("行业扩展表为空, 无法做行业中性化")

    with _cache_lock:
        _cache[key] = (mtime, out)
    return out


def attach_industry_l1(frame: pl.DataFrame, data_dir: Path | str | None) -> pl.DataFrame:
    """左联一级行业列; 缺 symbol 列或扩展表不可用时 fail-closed。"""
    if INDUSTRY_COLUMN in frame.columns:
        return frame
    if "symbol" not in frame.columns:
        raise ValueError("行业中性化需要面板包含 symbol 列")
    return frame.join(load_industry_l1(data_dir), on="symbol", how="left")


def industry_loader_for(data_dir: Path | str | None):
    """构造注入 StrategyEngine 的无参加载器。"""

    def _load() -> pl.DataFrame:
        return load_industry_l1(data_dir)

    return _load
