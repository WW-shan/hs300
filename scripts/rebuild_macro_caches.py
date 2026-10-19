"""Rebuild the macro caches used by build_alt_factor_data.py."""
from __future__ import annotations

from pathlib import Path

import polars as pl

CACHE = Path(__file__).resolve().parents[1] / "data" / "research" / "macro"


def main() -> int:
    import akshare as ak

    CACHE.mkdir(parents=True, exist_ok=True)
    targets = {
        "shibor": lambda: ak.macro_china_shibor_all(),
        "pmi": lambda: ak.macro_china_pmi_yearly(),
        "cpi": lambda: ak.macro_china_cpi_yearly(),
        "ppi": lambda: ak.macro_china_ppi_yearly(),
        "money_supply": lambda: ak.macro_china_money_supply(),
        "northbound": lambda: ak.stock_hsgt_hist_em(symbol="北向资金"),
    }
    for name, fn in targets.items():
        path = CACHE / f"{name}.parquet"
        if path.exists() and path.stat().st_size > 0:
            print(f"{name}: cached {pl.read_parquet(path).height} rows", flush=True)
            continue
        try:
            frame = pl.from_pandas(fn())
            frame.write_parquet(path)
            print(f"{name}: {frame.height} rows cols={frame.columns}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: FAILED {type(exc).__name__}: {str(exc)[:180]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
