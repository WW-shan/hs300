"""R163 fallback: per-symbol 2018Q1-2019Q4 top-10 free-float holders.

The endpoint used here omits announcement metadata, so this cache is only a
coverage fallback. The canonical PIT source is fetch_r163_holder_batch.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = DATA / "research" / "r163_holder_2018"
MEMBERSHIP = DATA / "research" / "r157_membership" / "monthly_snapshots.parquet"
QUARTERS = ["20180331", "20180630", "20180930", "20181231", "20190331", "20190630", "20190930", "20191231"]
RETRIES = 2


def _symbols() -> list[str]:
    frame = pl.read_parquet(MEMBERSHIP)
    out = []
    for sym in frame["symbol"].unique().to_list():
        code = sym[:6]
        prefix = "sh" if sym.endswith(".SH") else "sz"
        out.append(f"{prefix}{code}")
    return sorted(out)


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    symbols = _symbols()
    print(f"symbols={len(symbols)} quarters={len(QUARTERS)}", flush=True)
    failures = {}
    fetched = cached = empty = 0
    t0 = time.perf_counter()
    for qi, quarter in enumerate(QUARTERS, start=1):
        qdir = CACHE / quarter
        qdir.mkdir(parents=True, exist_ok=True)
        for si, symbol in enumerate(symbols, start=1):
            path = qdir / f"{symbol}.parquet"
            if path.exists() and path.stat().st_size > 0:
                cached += 1
                continue
            last_error = None
            for attempt in range(RETRIES + 1):
                try:
                    raw = ak.stock_gdfx_free_top_10_em(symbol=symbol, date=quarter)
                    frame = pl.from_pandas(raw)
                    if frame.height == 0:
                        empty += 1
                    frame.write_parquet(path)
                    fetched += 1
                    last_error = ""
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                    if attempt < RETRIES:
                        time.sleep(0.4 * (attempt + 1))
            if last_error:
                failures[f"{quarter}:{symbol}"] = last_error
            if si % 100 == 0:
                elapsed = time.perf_counter() - t0
                print(f"q{qi}/{len(QUARTERS)} {si}/{len(symbols)} fetched={fetched} cached={cached} empty={empty} failed={len(failures)} elapsed={elapsed:.0f}s", flush=True)
        print(f"quarter {quarter} done: fetched={fetched} cached={cached} empty={empty} failed={len(failures)}", flush=True)

    manifest = {
        "symbols": len(symbols),
        "quarters": len(QUARTERS),
        "fetched": fetched,
        "cached": cached,
        "empty": empty,
        "failure_count": len(failures),
        "failure_rate": round(len(failures) / (len(symbols) * len(QUARTERS)), 6),
        "failures_sample": dict(list(failures.items())[:20]),
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print("manifest:", json.dumps(manifest, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
