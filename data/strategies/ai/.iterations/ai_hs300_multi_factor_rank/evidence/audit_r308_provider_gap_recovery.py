from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
MEMBERSHIP = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
KLINE_ROOT = ROOT / "data/kline_daily"
ENRICHED_ROOT = ROOT / "data/kline_daily_enriched"
AUXILIARY = ROOT / "data/research/r40_prices/bootstrap_missing.parquet"
R305 = EVIDENCE / "r305_member_bar_gap_audit.json"
PREREG = EVIDENCE / "r308_provider_gap_recovery_preregistration.json"
OUTPUT = EVIDENCE / "r308_provider_gap_recovery_audit.json"
PROVIDER_START = date(2020, 12, 31)
PROVIDER_END = date(2026, 10, 14)
BATCH_SIZE = 10


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha(path).encode("ascii"))
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def compact(frame: pl.DataFrame) -> list[dict]:
    return [
        {key: str(value) if hasattr(value, "isoformat") else value for key, value in row.items()}
        for row in frame.to_dicts()
    ]


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R308 result exists; refusing to overwrite evidence")

    prereg_hash = sha(PREREG)
    sidecar_hash = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar_hash:
        raise RuntimeError("R308 preregistration checksum mismatch")

    canonical_paths = sorted(KLINE_ROOT.glob("date=*/part.parquet"))
    if not canonical_paths:
        raise RuntimeError("No canonical daily partitions found")
    input_paths = [MEMBERSHIP, AUXILIARY, R305, *canonical_paths]
    before_inputs = {path.relative_to(ROOT).as_posix(): sha(path) for path in input_paths}
    before_daily_tree = tree_hash(KLINE_ROOT)
    before_enriched_tree = tree_hash(ENRICHED_ROOT)

    membership = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["date", "symbol"])
    )
    if membership.height == 0:
        raise RuntimeError("Membership is empty")
    member_keys = membership.select("date", "symbol")
    start, end = member_keys["date"].min(), member_keys["date"].max()

    canonical = (
        pl.scan_parquet([str(path) for path in canonical_paths], extra_columns="ignore")
        .select(["date", "symbol"])
        .filter(pl.col("date").is_between(start, end, closed="both"))
        .collect()
    )
    canonical_keys = canonical.select("date", "symbol").unique()
    absent = member_keys.join(canonical_keys, on=["date", "symbol"], how="anti").sort(["date", "symbol"])
    if absent.height != 3586:
        raise RuntimeError(f"Expected 3,586 absent keys from R305, reconstructed {absent.height}")
    if absent.select(["date", "symbol"]).n_unique() != absent.height:
        raise RuntimeError("R308 absent keys contain duplicates")

    r305 = json.loads(R305.read_text(encoding="utf-8"))
    if int(r305["exact_key_coverage"]["canonical_absent"]) != absent.height:
        raise RuntimeError("R305 aggregate does not match reconstructed absent keys")
    expected_by_year = r305["exact_key_coverage"]["absent_by_year"]
    actual_by_year = {
        str(year): count
        for year, count in sorted(Counter(str(value)[:4] for value in absent["date"].to_list()).items())
    }
    if {str(k): int(v) for k, v in expected_by_year.items()} != actual_by_year:
        raise RuntimeError("R305 absent-by-year aggregate does not match reconstruction")

    auxiliary = (
        pl.read_parquet(AUXILIARY, columns=["date", "symbol", "tradestatus", "price_mode"])
        .with_columns(pl.col("date").str.to_date(), pl.col("symbol").cast(pl.String))
        .filter(pl.col("price_mode") == "qfq")
        .select("date", "symbol", "tradestatus")
        .unique(subset=["date", "symbol"], keep="last")
    )
    statuses = absent.join(auxiliary, on=["date", "symbol"], how="left")
    status_counts = {
        str(row["tradestatus"]): int(row["len"])
        for row in statuses.group_by("tradestatus").len().iter_rows(named=True)
    }
    expected_status = {"1": 2988, "0": 58, "None": 540}
    if status_counts != expected_status:
        raise RuntimeError(f"R305 auxiliary status counts changed: {status_counts}")

    symbols = sorted(absent["symbol"].unique().to_list())
    if len(symbols) != 49:
        raise RuntimeError(f"Expected 49 affected symbols, got {len(symbols)}")

    sys.path.insert(0, str(ROOT / "backend"))
    from app.plugins.akshare.provider import AkShareProvider

    provider = AkShareProvider()
    frames = []
    errors: list[dict] = []
    for offset in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[offset:offset + BATCH_SIZE]
        try:
            frame = provider.get_daily(
                batch,
                PROVIDER_START,
                PROVIDER_END,
                asset_type="stock",
                on_chunk_done=lambda cur, total, base=offset: print(
                    f"provider {base + cur}/{len(symbols)} ({cur}/{total} in batch)", flush=True
                ),
            )
            frames.append(frame)
        except Exception as exc:  # noqa: BLE001 - preserve provider failure in evidence
            errors.append({
                "batch_start": offset,
                "symbols": batch,
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"provider batch {offset} failed: {exc}", flush=True)

    empty_schema = {
        "symbol": pl.String,
        "date": pl.Date,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Int64,
        "amount": pl.Float64,
    }
    provider_frame = (
        pl.concat(frames, how="vertical_relaxed")
        if frames else pl.DataFrame(schema=empty_schema)
    )
    provider_frame = (
        provider_frame.select(list(empty_schema))
        .filter(pl.col("date").is_between(PROVIDER_START, PROVIDER_END, closed="both"))
        .sort(["symbol", "date"])
    )
    provider_duplicates = provider_frame.group_by(["date", "symbol"]).len().filter(pl.col("len") > 1)
    if provider_duplicates.height:
        raise RuntimeError("Provider returned duplicate date-symbol keys")

    valid = provider_frame.filter(
        (pl.col("open") > 0)
        & (pl.col("high") > 0)
        & (pl.col("low") > 0)
        & (pl.col("close") > 0)
        & (pl.col("volume") >= 0)
    ).select("date", "symbol").unique()
    invalid = provider_frame.select("date", "symbol").unique().join(
        valid, on=["date", "symbol"], how="anti"
    )
    returned_symbols = provider_frame.select("symbol").unique()
    classifications = (
        absent.join(valid.with_columns(pl.lit("provider_valid_bar").alias("_provider_class")), on=["date", "symbol"], how="left")
        .join(invalid.with_columns(pl.lit("provider_invalid_bar").alias("_provider_class_invalid")), on=["date", "symbol"], how="left")
        .join(returned_symbols.with_columns(pl.lit(True).alias("_provider_symbol_returned")), on="symbol", how="left")
        .with_columns(
            pl.when(pl.col("_provider_class").is_not_null()).then(pl.col("_provider_class"))
            .when(pl.col("_provider_class_invalid").is_not_null()).then(pl.col("_provider_class_invalid"))
            .when(pl.col("_provider_symbol_returned").fill_null(False)).then(pl.lit("provider_absent_for_returned_symbol"))
            .otherwise(pl.lit("provider_absent_empty_symbol"))
            .alias("provider_class")
        )
        .join(auxiliary, on=["date", "symbol"], how="left")
    )
    by_provider_class = {
        str(row["provider_class"]): int(row["len"])
        for row in classifications.group_by("provider_class").len().iter_rows(named=True)
    }
    by_aux_status = {
        str(row["tradestatus"]): int(row["len"])
        for row in classifications.group_by("tradestatus").len().iter_rows(named=True)
    }
    valid_by_status = {
        str(row["tradestatus"]): int(row["len"])
        for row in classifications.filter(pl.col("provider_class") == "provider_valid_bar")
        .group_by("tradestatus").len().iter_rows(named=True)
    }
    valid_by_year = {
        str(row["year"]): int(row["len"])
        for row in classifications.filter(pl.col("provider_class") == "provider_valid_bar")
        .with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year").len().iter_rows(named=True)
    }
    by_symbol = classifications.group_by("symbol").agg(
        pl.len().alias("absent_keys"),
        (pl.col("provider_class") == "provider_valid_bar").sum().alias("provider_valid"),
        (pl.col("provider_class") == "provider_absent_for_returned_symbol").sum().alias("provider_absent"),
        (pl.col("provider_class") == "provider_absent_empty_symbol").sum().alias("provider_empty_symbol"),
    ).sort(["absent_keys", "symbol"], descending=[True, False])

    provider_symbol_rows = (
        provider_frame.group_by("symbol").len().sort(["len", "symbol"], descending=[True, False])
    )
    returned_symbol_count = int(returned_symbols.height)
    valid_count = int(valid.height)
    unresolved_count = int(status_counts.get("None", 0))
    unresolved_valid = int(valid_by_status.get("None", 0))
    provider_symbol_coverage = returned_symbol_count / len(symbols) if symbols else 0.0
    classification_total = sum(by_provider_class.values())
    if classification_total != absent.height:
        raise RuntimeError(f"Provider classification partition mismatch: {classification_total}")

    after_inputs = {path.relative_to(ROOT).as_posix(): sha(path) for path in input_paths}
    after_daily_tree = tree_hash(KLINE_ROOT)
    after_enriched_tree = tree_hash(ENRICHED_ROOT)
    inputs_stable = before_inputs == after_inputs
    trees_stable = before_daily_tree == after_daily_tree and before_enriched_tree == after_enriched_tree
    checks = {
        "P1_exact_absent_key_reconstruction": absent.height == 3586,
        "P2_provider_symbol_coverage": provider_symbol_coverage >= 0.95,
        "P3_classification_partition": classification_total == absent.height,
        "P4_no_write_or_input_drift": inputs_stable and trees_stable,
        "P5_no_outcome_fields": True,
    }
    strong_recovery = valid_count / absent.height >= 0.80
    unresolved_recovery = unresolved_valid / unresolved_count >= 0.50
    result = {
        "round": "R308",
        "classification": "preregistered read-only provider key-presence audit; no forward returns or alpha",
        "captured_at_utc": None,
        "preregistration_sha256": prereg_hash,
        "audit_script_sha256": sha(Path(__file__).resolve()),
        "provider": {
            "name": "akshare",
            "method": "AkShareProvider.get_daily",
            "window": [str(PROVIDER_START), str(PROVIDER_END)],
            "symbols_requested": len(symbols),
            "symbols_with_rows": returned_symbol_count,
            "symbol_coverage_ratio": round(provider_symbol_coverage, 8),
            "rows_returned_in_window": provider_frame.height,
            "duplicate_keys": provider_duplicates.height,
            "batch_size": BATCH_SIZE,
            "batch_errors": errors,
        },
        "inputs": {
            "r78_rows": membership.height,
            "r78_symbols": membership["symbol"].n_unique(),
            "canonical_absent_keys": absent.height,
            "affected_symbols": len(symbols),
            "auxiliary_status_counts": status_counts,
            "before": before_inputs,
            "after_match": inputs_stable,
            "daily_tree": before_daily_tree,
            "daily_tree_after": after_daily_tree,
            "enriched_tree": before_enriched_tree,
            "enriched_tree_after": after_enriched_tree,
        },
        "classification": {
            "counts": by_provider_class,
            "by_auxiliary_status": by_aux_status,
            "provider_valid_by_auxiliary_status": valid_by_status,
            "provider_valid_by_year": valid_by_year,
            "valid_key_coverage_ratio": round(valid_count / absent.height, 8),
            "unresolved_valid_count": unresolved_valid,
            "unresolved_valid_ratio": round(unresolved_valid / unresolved_count, 8) if unresolved_count else None,
            "by_symbol": compact(by_symbol),
        },
        "gates": checks,
        "decision": {
            "strong_recovery": strong_recovery,
            "unresolved_recovery": unresolved_recovery,
            "recommendation": (
                "separate canonical backfill review"
                if strong_recovery or unresolved_recovery
                else "record as unresolved; do not impute or backfill"
            ),
            "production_write": False,
            "backtest_run": False,
            "alpha_scan_run": False,
        },
        "limits": [
            "AkShare/Sina absence is not proof of suspension or delisting.",
            "The provider returns unadjusted bars; no adjusted prices or returns are calculated.",
            "No row is written to canonical or enriched data in this round.",
            "The result is a data-provenance diagnostic, not an alpha estimate or deployment decision.",
        ],
    }
    from datetime import UTC, datetime
    result["captured_at_utc"] = datetime.now(UTC).isoformat()
    raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "absent_keys": absent.height,
        "provider_symbol_coverage": provider_symbol_coverage,
        "provider_valid_bar": valid_count,
        "provider_valid_ratio": valid_count / absent.height,
        "unresolved_valid_count": unresolved_valid,
        "unresolved_valid_ratio": unresolved_valid / unresolved_count if unresolved_count else None,
        "by_provider_class": by_provider_class,
        "gates": checks,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
