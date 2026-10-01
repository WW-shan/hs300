from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.hs300.service import HS300MembershipError, HS300Service


def _snapshot(root: Path, year: int, month: int, symbols: list[str]) -> None:
    path = root / "docs" / f"{year:04d}" / f"{month:02d}" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True)
    pl.DataFrame(
        {
            "Symbol": symbols,
            "Name": [f"stock-{i}" for i in range(len(symbols))],
        }
    ).write_csv(path)


def test_snapshot_dates_are_sorted(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 8, ["600519.SS"])
    _snapshot(tmp_path, 2023, 7, ["000001.SZ"])

    assert HS300Service(tmp_path / "docs").snapshot_dates() == [
        date(2023, 7, 1),
        date(2023, 8, 1),
    ]


def test_members_select_latest_snapshot_on_or_before_date(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS", "000001.SZ", "430047.BJ"])
    _snapshot(tmp_path, 2023, 8, ["600519.SS", "300750.SZ"])
    service = HS300Service(tmp_path / "docs")

    frame = service.members(date(2023, 7, 31))

    assert frame["symbol"].to_list() == ["600519.SH", "000001.SZ", "430047.BJ"]
    assert frame["effective_date"].to_list() == [date(2023, 7, 1)] * 3
    assert frame["snapshot_date"].to_list() == [date(2023, 7, 1)] * 3
    assert frame["source"].to_list() == ["index-constituents"] * 3


def test_dates_before_first_snapshot_fail_explicitly(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS"])
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="CSI300 snapshots begin"):
        service.members(date(2023, 6, 30))


def test_members_between_returns_union_with_snapshot_dates(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS", "000001.SZ"])
    _snapshot(tmp_path, 2023, 8, ["600519.SS", "300750.SZ"])
    _snapshot(tmp_path, 2023, 9, ["600519.SS", "601318.SS"])
    service = HS300Service(tmp_path / "docs")

    frame = service.members_between(date(2023, 7, 15), date(2023, 8, 31))

    assert frame["symbol"].to_list() == [
        "600519.SH",
        "000001.SZ",
        "600519.SH",
        "300750.SZ",
    ]
    assert frame["effective_date"].to_list() == [
        date(2023, 7, 1),
        date(2023, 7, 1),
        date(2023, 8, 1),
        date(2023, 8, 1),
    ]


def test_members_between_rejects_inverted_range(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS"])
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="start must be on or before end"):
        service.members_between(date(2023, 8, 1), date(2023, 7, 31))


def test_members_after_last_snapshot_fall_back_to_latest(tmp_path: Path) -> None:
    _snapshot(tmp_path, 2023, 7, ["600519.SS"])
    service = HS300Service(tmp_path / "docs")

    frame = service.members(date(2026, 9, 28))

    assert frame["symbol"].to_list() == ["600519.SH"]
    assert frame["effective_date"].to_list() == [date(2023, 7, 1)]


def test_empty_snapshot_root_fails_explicitly(tmp_path: Path) -> None:
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="No CSI300 snapshot is available"):
        service.members(date(2023, 7, 31))


def test_snapshot_missing_required_columns_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "2023" / "07" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"Ticker": ["600519.SS"], "Name": ["贵州茅台"]}).write_csv(path)
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="missing columns"):
        service.members(date(2023, 7, 31))


def test_snapshot_with_blank_symbol_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "2023" / "07" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True)
    pl.DataFrame({"Symbol": ["600519.SS", "   "], "Name": ["贵州茅台", "空白"]}).write_csv(path)
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="blank or null symbols"):
        service.members(date(2023, 7, 31))


def test_snapshot_with_header_only_fails_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "2023" / "07" / "constituents-csi300.csv"
    path.parent.mkdir(parents=True)
    path.write_text("Symbol,Name\n", encoding="utf-8")
    service = HS300Service(tmp_path / "docs")

    with pytest.raises(HS300MembershipError, match="contains no members"):
        service.members(date(2023, 7, 31))
