from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISOLATION = (
    ROOT
    / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r362_isolation.py"
)


def _module():
    spec = importlib.util.spec_from_file_location("r362_isolation_test", ISOLATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_isolation_materializes_alt_factors_regime_history_and_custom_factor(tmp_path: Path) -> None:
    source = tmp_path / "source"
    engine = tmp_path / "engine"
    (source / "ext_data/alt_hs300/timeseries/date=2026-11-05").mkdir(parents=True)
    (source / "user_data/custom_factors").mkdir(parents=True)
    (source / "user_data/custom_signals").mkdir(parents=True)
    (source / "regime_history").mkdir(parents=True)
    (source / "kline_daily_enriched/date=2026-11-05").mkdir(parents=True)
    (source / "kline_daily/date=2026-11-05").mkdir(parents=True)
    (source / "ext_data/alt_hs300/config.json").write_text("{}", encoding="utf-8")
    (source / "ext_data/alt_hs300/timeseries/date=2026-11-05/part.parquet").write_bytes(b"factor")
    (source / "user_data/custom_factors/uf_intraday_reversal.json").write_text("{}", encoding="utf-8")
    (source / "user_data/custom_signals/example.json").write_text("{}", encoding="utf-8")
    (source / "regime_history/part.parquet").write_bytes(b"regime")
    (source / "kline_daily_enriched/date=2026-11-05/part.parquet").write_bytes(b"enriched")
    (source / "kline_daily/date=2026-11-05/part.parquet").write_bytes(b"raw")

    engine.mkdir()
    for name in ("ext_data", "user_data", "regime_history", "kline_daily_enriched", "kline_daily"):
        (engine / name).symlink_to(source / name, target_is_directory=True)

    report = _module().prepare_isolated_data_root(engine, source)

    assert report["isolated"] is True
    assert report["remaining_top_level_symlinks"] == []
    assert not (engine / "ext_data").is_symlink()
    assert not (engine / "user_data").is_symlink()
    assert not (engine / "regime_history").is_symlink()
    assert not (engine / "kline_daily_enriched").is_symlink()
    assert not (engine / "kline_daily").is_symlink()
    assert (engine / "ext_data/alt_hs300/timeseries/date=2026-11-05/part.parquet").read_bytes() == b"factor"
    assert (engine / "user_data/custom_factors/uf_intraday_reversal.json").exists()
    assert (engine / "user_data/custom_signals/example.json").exists()
    assert (engine / "regime_history/part.parquet").read_bytes() == b"regime"
