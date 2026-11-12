from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path


def _replace_without_following_symlink(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _latest_partition_date(path: Path) -> date | None:
    partitions = sorted(path.glob("date=*"))
    if not partitions:
        return None
    return date.fromisoformat(partitions[-1].name.split("=", 1)[1])


def prepare_isolated_data_root(engine_root: Path, source_data: Path) -> dict:
    """Materialize every store input that the R361 backtest may read or mutate."""
    engine_root = Path(engine_root)
    source_data = Path(source_data)
    if not engine_root.exists() or engine_root.resolve() == source_data.resolve():
        raise ValueError("isolated engine root must be a distinct existing directory")

    materialized_roots: list[str] = []
    for link in sorted(engine_root.iterdir()):
        if not link.is_symlink() or link.name in {"ext_data", "user_data"}:
            continue
        source = link.resolve(strict=True)
        _replace_without_following_symlink(link)
        if source.is_dir():
            shutil.copytree(source, link)
        else:
            shutil.copy2(source, link)
        materialized_roots.append(link.name)

    _replace_without_following_symlink(engine_root / "ext_data")
    shutil.copytree(source_data / "ext_data", engine_root / "ext_data", symlinks=False)

    _replace_without_following_symlink(engine_root / "user_data")
    user_root = engine_root / "user_data"
    user_root.mkdir(parents=True)
    for namespace in ("custom_factors", "custom_signals"):
        source = source_data / "user_data" / namespace
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copytree(source, user_root / namespace)

    enriched = engine_root / "kline_daily_enriched"
    latest = _latest_partition_date(enriched)
    copied = 0
    for source in sorted((source_data / "kline_daily_enriched").glob("date=*/part.parquet")):
        day = date.fromisoformat(source.parent.name.split("=", 1)[1])
        if latest is not None and day <= latest:
            continue
        target = enriched / source.parent.name / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1

    raw_daily = engine_root / "kline_daily"
    if raw_daily.is_symlink() or not raw_daily.exists():
        _replace_without_following_symlink(raw_daily)
        shutil.copytree(source_data / "kline_daily", raw_daily)

    return {
        "isolated": True,
        "materialized_paths": [*materialized_roots, "ext_data", "user_data/custom_factors", "user_data/custom_signals"],
        "copied_enriched_partitions_after_r40": copied,
        "remaining_top_level_symlinks": [path.name for path in engine_root.iterdir() if path.is_symlink()],
        "raw_daily_is_symlink": raw_daily.is_symlink(),
        "alternative_timeseries_is_symlink": (engine_root / "ext_data/alt_hs300/timeseries").is_symlink(),
    }
