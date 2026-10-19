"""Rebuild the r43 LHB caches from AkShare (detail + jgmm monthly)."""
from __future__ import annotations

import calendar
import time
from datetime import date
from pathlib import Path

import polars as pl

CACHE = Path(__file__).resolve().parents[1] / "data" / "research" / "r43_lhb"
START = date(2021, 6, 1)
END = date(2026, 10, 16)


def _months() -> list[tuple[int, int]]:
    out = []
    y, m = START.year, START.month
    while (y, m) <= (END.year, END.month):
        out.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    failures = {}
    fetched = cached = 0
    for index, (year, month) in enumerate(_months(), start=1):
        start = f"{year:04d}{month:02d}01"
        end = f"{year:04d}{month:02d}{calendar.monthrange(year, month)[1]:02d}"
        detail_path = CACHE / f"detail_{year:04d}-{month:02d}.parquet"
        jgmm_path = CACHE / f"jgmm_{year:04d}-{month:02d}.parquet"
        for label, fn, path in (
            ("detail", lambda s=start, e=end: ak.stock_lhb_detail_em(start_date=s, end_date=e), detail_path),
            ("jgmm", lambda s=start, e=end: ak.stock_lhb_jgmmtj_em(start_date=s, end_date=e), jgmm_path),
        ):
            if path.exists() and path.stat().st_size > 0:
                cached += 1
                continue
            last_error = None
            for attempt in range(3):
                try:
                    frame = fn()
                    pl.from_pandas(frame).write_parquet(path)
                    fetched += 1
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{type(exc).__name__}: {str(exc)[:150]}"
                    if attempt < 2:
                        time.sleep(1.0 * (attempt + 1))
            if last_error:
                failures[f"{label}:{year}-{month:02d}"] = last_error
        if index % 6 == 0 or index == len(_months()):
            print(f"lhb {index}/{len(_months())} cached={cached} fetched={fetched} failed={len(failures)}", flush=True)
    print("failures:", failures, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
