from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISOLATION = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r360_isolation.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r360_isolation", ISOLATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_isolation_materializes_research_factors_signals_and_history(tmp_path: Path) -> None:
    source = tmp_path / "source"
    engine = tmp_path / "engine"
    (source / "ext_data/alt_hs300/timeseries/date=2026-01-02").mkdir(parents=True)
    (source / "ext_data/ext_hy_ths").mkdir(parents=True)
    (source / "ext_data/ext_gn_ths").mkdir(parents=True)
    (source / "user_data/custom_factors").mkdir(parents=True)
    (source / "user_data/custom_signals").mkdir(parents=True)
    (source / "financials/income").mkdir(parents=True)
    (source / "kline_daily_enriched/date=2026-01-02").mkdir(parents=True)
    (source / "kline_daily/date=2026-01-02").mkdir(parents=True)
    (source / "kline_daily/date=2026-01-02/part.parquet").write_bytes(b"raw")
    (source / "ext_data/alt_hs300/config.json").write_text("{}", encoding="utf-8")
    (source / "ext_data/alt_hs300/timeseries/date=2026-01-02/part.parquet").write_bytes(b"factor")
    (source / "ext_data/ext_hy_ths/part.parquet").write_bytes(b"industry")
    (source / "ext_data/ext_gn_ths/config.json").write_text("{}", encoding="utf-8")
    (source / "user_data/custom_factors/uf_example.json").write_text("{}", encoding="utf-8")
    (source / "user_data/custom_signals/csg_example.json").write_text("{}", encoding="utf-8")
    (source / "financials/income/part.parquet").write_bytes(b"financial")
    (source / "kline_daily_enriched/date=2026-01-02/part.parquet").write_bytes(b"old")

    engine.mkdir()
    (engine / "ext_data").symlink_to(source / "ext_data", target_is_directory=True)
    (engine / "user_data").symlink_to(source / "user_data", target_is_directory=True)
    (engine / "financials").symlink_to(source / "financials", target_is_directory=True)
    (engine / "kline_daily_enriched/date=2026-01-02").mkdir(parents=True)
    (engine / "kline_daily_enriched/date=2026-01-02/part.parquet").write_bytes(b"r40")
    (engine / "kline_daily").symlink_to(source / "kline_daily", target_is_directory=True)
    (source / "kline_daily_enriched/date=2026-01-03").mkdir(parents=True)
    (source / "kline_daily_enriched/date=2026-01-03/part.parquet").write_bytes(b"new")

    report = _module().prepare_isolated_data_root(engine, source)

    assert report["isolated"] is True
    assert report["copied_enriched_partitions_after_r40"] == 1
    assert not (engine / "ext_data").is_symlink()
    assert not (engine / "user_data").is_symlink()
    assert not (engine / "financials").is_symlink()
    assert not (engine / "kline_daily").is_symlink()
    assert report["remaining_top_level_symlinks"] == []
    assert not (engine / "ext_data/alt_hs300/timeseries").is_symlink()
    assert (engine / "ext_data/alt_hs300/timeseries/date=2026-01-02/part.parquet").read_bytes() == b"factor"
    assert (engine / "ext_data/ext_gn_ths/config.json").exists()
    assert json.loads((engine / "user_data/custom_factors/uf_example.json").read_text()) == {}
    assert (engine / "user_data/custom_signals/csg_example.json").exists()
    assert (engine / "financials/income/part.parquet").read_bytes() == b"financial"
    assert (engine / "kline_daily_enriched/date=2026-01-03/part.parquet").read_bytes() == b"new"

    (source / "ext_data/alt_hs300/timeseries/date=2026-01-02/part.parquet").write_bytes(b"changed")
    assert (engine / "ext_data/alt_hs300/timeseries/date=2026-01-02/part.parquet").read_bytes() == b"factor"
