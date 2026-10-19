"""Build only the alternative-factor subset needed by R112.

R112 uses the macro-gate quality sleeve and the industry relative-strength
sleeve.  This script therefore materializes only:

- holder_top10_free_float_share
- equity_risk_premium
- credit_spread_5y_low
- term_spread_10y_2y
- stock_rel_industry_20d
- industry_mom_20d

All other alternative-factor caches are intentionally left untouched.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import polars as pl

from app.factors import ext_factors
from app.services.ext_data import ExtConfig, ExtConfigStore
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BUILDER = ROOT / "scripts" / "build_alt_factor_data.py"
CONFIG_ID = "alt_hs300"
NEEDED = {
    "holder_top10_free_float_share",
    "equity_risk_premium",
    "credit_spread_5y_low",
    "term_spread_10y_2y",
    "stock_rel_industry_20d",
    "industry_mom_20d",
}


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_alt_factor_data", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_partitions(frame: pl.DataFrame) -> int:
    out_root = DATA / "ext_data" / CONFIG_ID / "timeseries"
    if out_root.exists():
        import shutil

        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    count = 0
    for part in frame.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
        day = part["date"][0]
        target = out_root / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(part.drop("date"), target)
        count += 1
    return count


def main() -> int:
    module = _load_builder()
    dates, symbols = module._calendar()
    print(f"calendar={len(dates)} symbols={len(symbols)}", flush=True)

    holder = module._holder_daily(dates, symbols).select([
        "symbol", "date", "holder_top10_free_float_share",
    ])
    industry = module._industry_daily(dates, symbols).select([
        "symbol", "date", "stock_rel_industry_20d", "industry_mom_20d",
    ])
    erp = module._erp_daily(dates).select(["date", "equity_risk_premium"])
    macro = module._macro_daily(dates).select([
        "date", "credit_spread_5y_low", "term_spread_10y_2y",
    ])

    base = (
        module._calendar_frame(dates, symbols)
        .join(holder, on=["symbol", "date"], how="left")
        .join(industry, on=["symbol", "date"], how="left")
        .join(erp, on="date", how="left")
        .join(macro, on="date", how="left")
        .select(["symbol", "date", *sorted(NEEDED)])
    )
    fields = [field for field in module.FIELDS if field.name in NEEDED]
    if len(fields) != len(NEEDED):
        raise RuntimeError("alternative-factor field definition mismatch")

    cfg = ExtConfig(
        id=CONFIG_ID,
        label="HS300另类因子(R112子集)",
        mode="timeseries",
        fields=fields,
        description="R112双袖风险平价所需的最小另类因子子集。",
    )
    ExtConfigStore(DATA).upsert(cfg)
    partitions = _write_partitions(base)
    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    print(
        f"wrote rows={base.height} partitions={partitions} "
        f"factors={[spec.id for spec in ext_factors.ext_factor_specs(DATA)]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())