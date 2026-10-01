"""Read point-in-time CSI 300 membership from local snapshot files."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

MIN_SNAPSHOT_DATE = date(2023, 7, 1)
SNAPSHOT_FILENAME = "constituents-csi300.csv"
SOURCE = "index-constituents"


class HS300MembershipError(ValueError):
    """Raised when CSI 300 membership cannot be resolved safely."""


class HS300Service:
    """Access monthly CSI 300 constituent snapshots without network calls."""

    def __init__(self, snapshot_root: Path | str):
        self._snapshot_root = Path(snapshot_root)

    def snapshot_dates(self) -> list[date]:
        """Return available snapshot effective dates in chronological order.

        Only ``YYYY/MM/constituents-csi300.csv`` archives are considered. The
        top-level ``constituents-csi300.csv`` "current" file is intentionally
        ignored because the monthly archive already carries the same snapshot.
        """
        dates: set[date] = set()
        for path in self._snapshot_root.glob(f"????/??/{SNAPSHOT_FILENAME}"):
            year_text, month_text = path.parts[-3:-1]
            try:
                dates.add(date(int(year_text), int(month_text), 1))
            except ValueError:
                continue
        return sorted(dates)

    def members(self, as_of: date) -> pl.DataFrame:
        """Return members from the latest snapshot effective on or before ``as_of``."""
        snapshot_date = self._resolve_snapshot_date(as_of)
        return self._read_snapshot(snapshot_date)

    def members_between(self, start: date, end: date) -> pl.DataFrame:
        """Return the snapshots in effect from ``start`` through ``end``.

        Snapshots are monthly, so a range beginning mid-month starts at the
        snapshot whose effective date is on or before ``start``; each row keeps
        its own effective and snapshot dates.
        """
        if start > end:
            raise HS300MembershipError("start must be on or before end")

        first_snapshot = self._resolve_snapshot_date(start)
        last_snapshot = self._resolve_snapshot_date(end)
        snapshot_dates = [
            snapshot_date
            for snapshot_date in self.snapshot_dates()
            if first_snapshot <= snapshot_date <= last_snapshot
        ]
        return pl.concat(
            [self._read_snapshot(snapshot_date) for snapshot_date in snapshot_dates],
            how="vertical",
        )

    def _resolve_snapshot_date(self, as_of: date) -> date:
        if as_of < MIN_SNAPSHOT_DATE:
            raise HS300MembershipError(
                f"CSI300 snapshots begin on {MIN_SNAPSHOT_DATE.isoformat()}; "
                f"requested {as_of.isoformat()}"
            )

        available = [
            snapshot_date for snapshot_date in self.snapshot_dates() if snapshot_date <= as_of
        ]
        if not available:
            raise HS300MembershipError(
                f"No CSI300 snapshot is available on or before {as_of.isoformat()}"
            )
        return available[-1]

    def _snapshot_path(self, snapshot_date: date) -> Path:
        return (
            self._snapshot_root
            / f"{snapshot_date.year:04d}"
            / f"{snapshot_date.month:02d}"
            / SNAPSHOT_FILENAME
        )

    def _read_snapshot(self, snapshot_date: date) -> pl.DataFrame:
        path = self._snapshot_path(snapshot_date)
        try:
            frame = pl.read_csv(path)
        except (OSError, pl.exceptions.PolarsError) as exc:
            raise HS300MembershipError(
                f"Failed to read CSI300 snapshot {snapshot_date}: {exc}"
            ) from exc

        missing = {"Symbol", "Name"} - set(frame.columns)
        if missing:
            raise HS300MembershipError(
                f"CSI300 snapshot {snapshot_date} is missing columns: {', '.join(sorted(missing))}"
            )
        if frame.is_empty():
            raise HS300MembershipError(
                f"CSI300 snapshot {snapshot_date} contains no members"
            )

        symbol = pl.col("Symbol").cast(pl.String, strict=False).str.strip_chars()
        if frame.select(symbol.is_null() | (symbol.str.len_chars() == 0)).to_series().any():
            raise HS300MembershipError(
                f"CSI300 snapshot {snapshot_date} contains blank or null symbols"
            )

        return frame.select(
            symbol.str.replace(r"\.SS$", ".SH").alias("symbol"),
            pl.col("Name").cast(pl.String, strict=False).str.strip_chars().alias("name"),
            pl.lit(snapshot_date, dtype=pl.Date).alias("effective_date"),
            pl.lit(snapshot_date, dtype=pl.Date).alias("snapshot_date"),
            pl.lit(SOURCE, dtype=pl.String).alias("source"),
        )
