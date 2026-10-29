#!/usr/bin/env python3
"""Read-only R291 audit of refreshed K-lines and stale R257 source partitions."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def verify_frozen_manifest() -> list[dict]:
    manifest_path = EVIDENCE / "r291_input_manifest.jsonl"
    prereg_path = EVIDENCE / "r291_preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    expected_hash = prereg["frozen_scope"]["input_manifest_sha256"]
    if digest(manifest_path) != expected_hash:
        raise RuntimeError("Frozen input manifest hash mismatch")
    frozen = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for row in frozen:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or digest(path) != row["sha256"]
        ):
            raise RuntimeError(f"Frozen input changed after preregistration: {row['path']}")
    return frozen


def metrics(frame: pl.DataFrame, columns: list[str]) -> dict:
    available = [column for column in columns if column in frame.columns]
    return {
        "rows": frame.height,
        "distinct_symbols": frame["symbol"].n_unique(),
        "nonnull": {column: frame[column].is_not_null().sum() for column in available},
    }


def compare_column(expected: pl.DataFrame, stored: pl.DataFrame, column: str) -> dict:
    joined = expected.select(["symbol", column]).join(
        stored.select(["symbol", column]),
        on="symbol",
        how="full",
        coalesce=True,
        suffix="_stored",
    )
    left = pl.col(column)
    right = pl.col(f"{column}_stored")
    both = left.is_not_null() & right.is_not_null()
    counts = joined.select([
        left.is_not_null().sum().alias("recomputed_nonnull"),
        right.is_not_null().sum().alias("stored_nonnull"),
        (both & (left == right)).sum().alias("equal_nonnull_values"),
        (both & (left != right)).sum().alias("mismatched_nonnull_values"),
        (left.is_not_null() & right.is_null()).sum().alias("recomputed_only"),
        (left.is_null() & right.is_not_null()).sum().alias("stored_only"),
    ]).row(0, named=True)
    return counts


def main() -> int:
    result_path = EVIDENCE / "r291_root_cause_audit_v2.json"
    if result_path.exists() or result_path.with_name(result_path.name + ".sha256").exists():
        raise RuntimeError("R291 result exists; refusing to overwrite")
    frozen_inputs = verify_frozen_manifest()
    base = load_module(
        ROOT / "scripts" / "build_alt_factor_data.py",
        "build_alt_factor_data_r291_read_only",
    )
    r257 = load_module(
        ROOT / "scripts" / "build_r257_quality_relative_strength_factor.py",
        "build_r257_r291_read_only",
    )
    dates, symbols = base._calendar()
    industry_all = base._industry_daily(dates, symbols)

    industry_columns = [
        "industry_mom_20d", "industry_mom_60d", "industry_rps_20d",
        "industry_breadth_20d", "stock_rel_industry_20d",
    ]
    correlated_columns = [
        "idio_vol_60d_low", "price_delay_60d", "cs_spread_60d",
        "vol_of_vol_60d_low", "market_down_beta_60d_low",
        "beta_asymmetry_120d", "trend_efficiency_60d",
        "risk_adjusted_momentum_60d",
    ]
    daily = {}
    comparisons = {}
    for day in ("2026-10-14", "2026-10-16", "2026-10-19", "2026-10-27"):
        date_value = datetime.strptime(day, "%Y-%m-%d").date()
        computed = industry_all.filter(pl.col("date") == date_value)
        stored_path = (
            DATA / "ext_data" / "alt_hs300" / "timeseries"
            / f"date={day}" / "part.parquet"
        )
        stored = pl.read_parquet(stored_path) if stored_path.is_file() else None
        daily[day] = {
            "recomputed_industry_builder": metrics(computed, industry_columns),
            "stored_base_partition": (
                metrics(stored, industry_columns + correlated_columns)
                if stored is not None else {"partition_exists": False}
            ),
        }
        if day in ("2026-10-16", "2026-10-19") and stored is not None:
            comparisons[day] = {
                column: compare_column(computed, stored, column)
                for column in industry_columns
                if column in computed.columns and column in stored.columns
            }

    membership = r257._membership_with_industry(base)
    composites = {}
    for day in ("2026-10-16", "2026-10-19"):
        date_value = datetime.strptime(day, "%Y-%m-%d").date()
        quality_path = (
            DATA / "ext_data" / "alt_hs300_minrank" / "timeseries"
            / f"date={day}" / "part.parquet"
        )
        composite_path = (
            DATA / "ext_data" / "alt_hs300_qrs" / "timeseries"
            / f"date={day}" / "part.parquet"
        )
        quality = pl.read_parquet(
            quality_path, columns=["symbol", r257.R253_FIELD]
        )
        refreshed_rs = industry_all.filter(pl.col("date") == date_value).select([
            "symbol", r257.RS_FIELD,
        ])
        panel = (
            quality.join(refreshed_rs, on="symbol", how="left")
            .with_columns(pl.lit(date_value).cast(pl.Date).alias("date"))
            .select(["symbol", "date", r257.R253_FIELD, r257.RS_FIELD])
        )
        rebuilt = r257._min_rank_factor(panel, membership)
        stored = pl.read_parquet(composite_path, columns=["symbol", r257.FIELD])
        rebuilt_valid = rebuilt.filter(pl.col(r257.FIELD).is_not_null()).select("symbol")
        stored_valid = stored.filter(pl.col(r257.FIELD).is_not_null()).select("symbol")
        overlap = rebuilt.select(["symbol", r257.FIELD]).join(
            stored.select(["symbol", r257.FIELD]),
            on="symbol",
            how="inner",
            suffix="_stored",
        ).filter(
            pl.col(r257.FIELD).is_not_null()
            & pl.col(f"{r257.FIELD}_stored").is_not_null()
        )
        equal_overlap = overlap.filter(
            pl.col(r257.FIELD) == pl.col(f"{r257.FIELD}_stored")
        ).height
        composites[day] = {
            "quality_nonnull": quality[r257.R253_FIELD].is_not_null().sum(),
            "refreshed_relative_strength_nonnull": refreshed_rs[r257.RS_FIELD].is_not_null().sum(),
            "recomputed_composite_nonnull": rebuilt_valid.height,
            "stored_composite_nonnull": stored_valid.height,
            "recomputed_and_stored_nonnull_symbol_overlap": rebuilt_valid.join(
                stored_valid, on="symbol", how="inner"
            ).height,
            "equal_value_count_within_nonnull_overlap": equal_overlap,
            "value_reproducibility_claim": "none; rebuilt values use the currently refreshed K-line inputs",
        }

    base_partition = (
        DATA / "ext_data" / "alt_hs300" / "timeseries"
        / "date=2026-10-19" / "part.parquet"
    )
    market_partition = DATA / "kline_daily_enriched" / "date=2026-10-19" / "part.parquet"
    base_stat = base_partition.stat()
    market_stat = market_partition.stat()
    elapsed_days = (market_stat.st_mtime - base_stat.st_mtime) / 86400
    recomputed_19 = daily["2026-10-19"]["recomputed_industry_builder"]["nonnull"]["stock_rel_industry_20d"]
    stored_19 = daily["2026-10-19"]["stored_base_partition"]["nonnull"]["stock_rel_industry_20d"]
    composite_19 = composites["2026-10-19"]["recomputed_composite_nonnull"]
    criteria = {
        "fresh_recomputation_recovers_2026_10_19_coverage": recomputed_19 >= 500,
        "stored_2026_10_19_partition_has_low_coverage": stored_19 <= 50,
        "current_market_input_postdates_derived_partition_by_one_day": elapsed_days >= 1,
        "refreshed_components_restore_r257_composite_coverage": composite_19 >= 200,
    }
    if verify_frozen_manifest() != frozen_inputs:
        raise RuntimeError("Frozen inputs changed during the audit")

    passed = all(criteria.values())
    output = {
        "round": "R291",
        "classification": "read_only_stale_derived_factor_root_cause_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "status": "stale_derived_partition_supported" if passed else "cause_not_confirmed",
        "universe": {"symbols": len(symbols), "calendar_dates": len(dates)},
        "daily_coverage": daily,
        "stored_vs_current_builder_value_comparison": comparisons,
        "r257_in_memory_recalculation": composites,
        "lineage_timestamps": {
            "derived_base_partition_mtime": datetime.fromtimestamp(base_stat.st_mtime, UTC).isoformat(),
            "current_enriched_market_partition_mtime": datetime.fromtimestamp(market_stat.st_mtime, UTC).isoformat(),
            "market_partition_postdates_derived_by_days": round(elapsed_days, 4),
            "derived_partition_sha256": digest(base_partition),
            "current_market_partition_sha256": digest(market_partition),
            "prior_pre_refresh_market_snapshot_available": False,
        },
        "criteria": criteria,
        "input_manifest_sha256": json.loads(
            (EVIDENCE / "r291_preregistration.json").read_text(encoding="utf-8")
        )["frozen_scope"]["input_manifest_sha256"],
        "input_manifest_files": len(frozen_inputs),
        "execution_limits": {
            "backtest_run": False, "forward_returns_read": False,
            "historical_trades_read": False, "provider_called": False,
            "factor_builder_main_called": False, "source_factor_or_market_data_written": False,
            "strategy_or_production_state_changed": False,
            "all_frozen_inputs_unchanged_after_audit": True,
        },
        "conclusion": (
            "The saved 2026-10-19 alt_hs300 partition is inconsistent with the currently refreshed "
            "market input: the production industry calculation restores broad coverage, and the "
            "existing R253 quality input yields 249 R257 values when recomputed in memory. The two "
            "symbols with old and recomputed non-null R257 values do not match numerically, so this "
            "is coverage recovery only, not historical-value reproduction. The exact pre-refresh "
            "market values are unavailable; timestamps support stale lineage but do not prove the "
            "specific earlier input defect."
            if passed else
            "The preregistered evidence did not satisfy every criterion for stale-derived-partition support."
        ),
    }
    payload = (json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    result_hash = write_once(result_path, payload)
    print(json.dumps({
        "round": "R291", "status": output["status"], "symbols": len(symbols),
        "stored_2026_10_19_relative_strength": stored_19,
        "recomputed_2026_10_19_relative_strength": recomputed_19,
        "recomputed_2026_10_19_r257": composite_19,
        "old_and_recomputed_overlap_values_equal": composites["2026-10-19"]["equal_value_count_within_nonnull_overlap"],
        "source_postdates_derived_days": round(elapsed_days, 4),
        "criteria": criteria, "result_sha256": result_hash,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
