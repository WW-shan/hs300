from __future__ import annotations

from pathlib import Path

import polars as pl

from app.services.ext_data import ExtConfig, ExtField
from app.services.market_overview_builder import _dimension_field, _ext_files


def test_dimension_field_ignores_numeric_industry_factors() -> None:
    config = ExtConfig(
        id="alt_hs300",
        label="研究因子",
        mode="timeseries",
        fields=[
            ExtField("industry_rps_20d", "float", "一级行业20日动量截面分位"),
            ExtField("industry", "string", "行业"),
        ],
    )

    assert _dimension_field(config, "industry") == "industry"


def test_dimension_field_skips_numeric_only_config() -> None:
    config = ExtConfig(
        id="alt_hs300",
        label="研究因子",
        mode="timeseries",
        fields=[ExtField("industry_rps_20d", "float", "一级行业20日动量截面分位")],
    )

    assert _dimension_field(config, "industry") is None


def test_ext_files_reads_only_latest_timeseries_partition(tmp_path: Path) -> None:
    root = tmp_path / "ext_data" / "demo" / "timeseries"
    for day in ("2025-01-01", "2025-01-02"):
        part = root / f"date={day}"
        part.mkdir(parents=True)
        pl.DataFrame({"symbol": ["000001.SZ"], "industry": ["银行"]}).write_parquet(
            part / "part.parquet"
        )
    config = ExtConfig(
        id="demo",
        label="演示",
        mode="timeseries",
        fields=[ExtField("industry", "string", "行业")],
    )

    files = _ext_files(tmp_path, config)

    assert files == [str(root / "date=2025-01-02" / "part.parquet")]
