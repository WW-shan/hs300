from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISOLATION = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r364_isolation.py"


def test_isolation_materializes_close_reversal_factor_and_market_regime(tmp_path: Path) -> None:
    source, engine = tmp_path / "source", tmp_path / "engine"
    for path in (
        source / "ext_data/alt_hs300/timeseries/date=2026-11-05",
        source / "user_data/custom_factors",
        source / "user_data/custom_signals",
        source / "regime_history",
        source / "kline_daily_enriched/date=2026-11-05",
        source / "kline_daily/date=2026-11-05",
    ):
        path.mkdir(parents=True)
    (source / "ext_data/alt_hs300/config.json").write_text("{}")
    (source / "ext_data/alt_hs300/timeseries/date=2026-11-05/part.parquet").write_bytes(b"factor")
    (source / "user_data/custom_factors/uf_close_position_reversal.json").write_text("{}")
    (source / "user_data/custom_signals/example.json").write_text("{}")
    (source / "regime_history/part.parquet").write_bytes(b"regime")
    (source / "kline_daily_enriched/date=2026-11-05/part.parquet").write_bytes(b"enriched")
    (source / "kline_daily/date=2026-11-05/part.parquet").write_bytes(b"raw")
    engine.mkdir()
    for name in ("ext_data", "user_data", "regime_history", "kline_daily_enriched", "kline_daily"):
        (engine / name).symlink_to(source / name, target_is_directory=True)

    spec = importlib.util.spec_from_file_location("r364_isolation_test", ISOLATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.prepare_isolated_data_root(engine, source)

    assert report["isolated"] is True
    assert report["remaining_top_level_symlinks"] == []
    assert not (engine / "user_data").is_symlink()
    assert (engine / "user_data/custom_factors/uf_close_position_reversal.json").exists()
    assert (engine / "regime_history/part.parquet").read_bytes() == b"regime"
