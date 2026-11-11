from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.factor_research.extended_parameter_data import (
    compare_factor_frames,
    load_event_panels,
    load_market_series,
)


def _write_instruments(root: Path) -> None:
    path = root / "instruments" / "instruments.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ"],
            "code": ["000001"],
        }
    ).write_parquet(path)


def _write_empty_event_sources(root: Path, *, include_lhb: bool = True) -> None:
    research = root / "research"
    if include_lhb:
        lhb = research / "r43_lhb"
        lhb.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            schema={
                "代码": pl.String,
                "上榜日": pl.Date,
                "龙虎榜净买额": pl.Float64,
                "市场总成交额": pl.Float64,
            }
        ).write_parquet(lhb / "detail_2024-01.parquet")
        pl.DataFrame(
            schema={
                "代码": pl.String,
                "上榜日期": pl.Date,
                "机构买入净额": pl.Float64,
                "市场总成交额": pl.Float64,
            }
        ).write_parquet(lhb / "jgmm_2024-01.parquet")
    insider = research / "r90_insider_trade"
    insider.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        schema={
            "symbol": pl.String,
            "announce_date": pl.Date,
            "direction": pl.String,
        }
    ).write_parquet(insider / "buy_2024.parquet")
    external = research / "external_r39"
    external.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        schema={
            "股票代码": pl.String,
            "最新公告日期": pl.Date,
        }
    ).write_parquet(external / "repurchase.parquet")
    pl.DataFrame(
        schema={
            "证券代码": pl.String,
            "交易日期": pl.Date,
            "成交额": pl.Float64,
            "折溢率": pl.Float64,
            "成交额/流通市值": pl.Float64,
        }
    ).write_parquet(external / "dzjy_2024-01.parquet")
    analyst = research / "r47_analyst"
    analyst.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        schema={
            "symbol": pl.String,
            "report_date": pl.Date,
            "rating_score": pl.Float64,
            "rating_change_score": pl.Float64,
        }
    ).write_parquet(analyst / "2024-01-02.parquet")


def test_lhb_loader_builds_signed_ratio_institution_ratio_and_count(
    tmp_path: Path,
) -> None:
    _write_instruments(tmp_path)
    _write_empty_event_sources(tmp_path, include_lhb=False)
    lhb_root = tmp_path / "research" / "r43_lhb"
    lhb_root.mkdir(parents=True)
    day = date(2024, 1, 2)
    pl.DataFrame(
        {
            "代码": ["000001", "000001"],
            "上榜日": [day, day],
            "龙虎榜净买额": [10.0, 10.0],
            "市场总成交额": [1000.0, 1000.0],
        }
    ).write_parquet(lhb_root / "detail_2024-01.parquet")
    pl.DataFrame(
        {
            "代码": ["000001"],
            "上榜日期": [day],
            "机构买入净额": [5.0],
            "市场总成交额": [1000.0],
        }
    ).write_parquet(lhb_root / "jgmm_2024-01.parquet")

    panels = load_event_panels(tmp_path, ["000001.SZ"])
    row = panels["lhb"].row(0, named=True)

    assert row["net_buy_ratio"] == pytest.approx(2.0)
    assert row["inst_net_buy_ratio"] == pytest.approx(0.5)
    assert row["event_count"] == 2


def test_insider_loader_marks_at_most_one_event_per_direction_and_day(
    tmp_path: Path,
) -> None:
    _write_instruments(tmp_path)
    _write_empty_event_sources(tmp_path)
    source = tmp_path / "research" / "r90_insider_trade"
    source.mkdir(parents=True, exist_ok=True)
    day = date(2024, 1, 2)
    pl.DataFrame(
        {
            "symbol": ["000001.SZ", "000001.SZ", "000001.SZ"],
            "announce_date": [day, day, day],
            "direction": ["buy", "buy", "sell"],
        }
    ).write_parquet(source / "buy_2024.parquet")

    panel = load_event_panels(tmp_path, ["000001.SZ"])["insider"]
    row = panel.row(0, named=True)

    assert row["buy"] == 1.0
    assert row["sell"] == 1.0


def test_market_series_loader_selects_510300_and_index_series(tmp_path: Path) -> None:
    research = tmp_path / "research"
    (research / "r193_treasury").mkdir(parents=True)
    (research / "r195_hk").mkdir(parents=True)
    (research / "r74_etf_flow").mkdir(parents=True)
    (research / "r75_qvix").mkdir(parents=True)
    day = date(2024, 1, 2)
    pl.DataFrame({"date": [day], "close": [100.0]}).write_parquet(
        research / "r193_treasury" / "T0.parquet"
    )
    pl.DataFrame({"date": [day], "close": [101.0]}).write_parquet(
        research / "r193_treasury" / "TF0.parquet"
    )
    pl.DataFrame({"date": [day], "close": [200.0]}).write_parquet(
        research / "r195_hk" / "HSI.parquet"
    )
    pl.DataFrame({"date": [day], "close": [150.0]}).write_parquet(
        research / "r195_hk" / "HSCEI.parquet"
    )
    pl.DataFrame(
        {
            "基金代码": ["510300", "510500"],
            "统计日期": [day, day],
            "基金份额": [1000.0, 500.0],
        }
    ).write_parquet(research / "r74_etf_flow" / "2024-01.parquet")
    pl.DataFrame({"date": [day], "close": [20.0]}).write_parquet(
        research / "r75_qvix" / "qvix_300etf.parquet"
    )

    series = load_market_series(tmp_path)

    assert series["T0"]["close"].to_list() == [100.0]
    assert series["HSI"]["close"].to_list() == [200.0]
    assert series["etf_510300_shares"]["value"].to_list() == [1000.0]
    assert series["qvix_300etf"]["close"].to_list() == [20.0]


def test_compare_factor_frames_reports_key_coverage_and_value_error() -> None:
    generated = pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "factor_value": [1.0, 2.0],
        }
    )
    existing = pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "factor_value": [1.0, 2.5],
        }
    )

    result = compare_factor_frames(generated, existing, "factor_value")

    assert result["status"] == "fail"
    assert result["key_overlap_ratio"] == 1.0
    assert result["max_abs_error"] == pytest.approx(0.5)


def test_compare_factor_frames_rejects_reference_null_coverage_drift() -> None:
    generated = pl.DataFrame(
        {
            "symbol": ["A"],
            "date": [date(2024, 1, 2)],
            "factor_value": [1.0],
        }
    )
    existing = pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "factor_value": [1.0, 2.0],
        }
    )

    result = compare_factor_frames(generated, existing, "factor_value")

    assert result["status"] == "fail"
    assert result["key_overlap_ratio"] == 1.0
    assert result["reference_overlap_ratio"] == 0.5
    assert result["null_coverage_drift"] == 0.5
