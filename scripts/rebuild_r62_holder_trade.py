"""Rebuild the r62 holder-trade cache (stock_ggcg_em) as raw_all.parquet."""
from __future__ import annotations

from pathlib import Path

import polars as pl

CACHE = Path(__file__).resolve().parents[1] / "data" / "research" / "r62_holder_trade"


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "raw_all.parquet"
    if path.exists() and path.stat().st_size > 0:
        print("cached:", pl.read_parquet(path).height, "rows", flush=True)
        return 0
    frame = ak.stock_ggcg_em(symbol="全部")
    out = pl.from_pandas(frame)
    out.write_parquet(path)
    print("rows:", out.height, "cols:", out.columns, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
