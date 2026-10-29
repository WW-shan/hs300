from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
MEMBERSHIP = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
KLINE_ROOT = ROOT / "data/kline_daily"
AUXILIARY = ROOT / "data/research/r40_prices/bootstrap_missing.parquet"
PREREG = EVIDENCE / "r305_data_gap_diagnostic_preregistration.json"
OUTPUT = EVIDENCE / "r305_member_bar_gap_audit.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compact_rows(frame: pl.DataFrame) -> list[dict]:
    return [
        {key: str(value) if hasattr(value, "isoformat") else value for key, value in row.items()}
        for row in frame.to_dicts()
    ]


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R305 result exists; refusing to overwrite evidence")

    prereg_raw = PREREG.read_bytes()
    prereg_hash = hashlib.sha256(prereg_raw).hexdigest()
    sidecar_hash = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar_hash:
        raise RuntimeError("R305 diagnostic preregistration checksum mismatch")

    partitions = sorted(KLINE_ROOT.glob("date=*/part.parquet"))
    if not partitions:
        raise RuntimeError("No canonical daily K-line partitions found")
    input_paths = [MEMBERSHIP, AUXILIARY, *partitions]
    before_hashes = {str(path.relative_to(ROOT)): sha(path) for path in input_paths}

    membership = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
    member_keys = membership.select(pl.col("query_date").alias("date"), "symbol")
    duplicate_membership_keys = member_keys.group_by(["date", "symbol"]).len().filter(pl.col("len") > 1)
    if duplicate_membership_keys.height:
        raise RuntimeError(f"R78 contains {duplicate_membership_keys.height} duplicate date-symbol keys")

    canonical = (
        pl.scan_parquet(str(KLINE_ROOT / "date=*/part.parquet"))
        .select(["date", "symbol"])
        .filter(
            pl.col("date").is_between(
                membership["query_date"].min(), membership["query_date"].max(), closed="both"
            )
        )
        .collect()
    )
    duplicates = canonical.group_by(["date", "symbol"]).len().filter(pl.col("len") > 1)
    canonical_keys = canonical.select("date", "symbol").unique()
    absent = member_keys.join(canonical_keys, on=["date", "symbol"], how="anti").sort(["date", "symbol"])

    auxiliary = (
        pl.read_parquet(AUXILIARY, columns=["date", "symbol", "tradestatus", "price_mode"])
        .with_columns(pl.col("date").str.to_date())
        .filter(pl.col("price_mode") == "qfq")
        .select(["date", "symbol", "tradestatus"])
    )
    auxiliary_duplicates = auxiliary.group_by(["date", "symbol"]).agg(
        pl.len().alias("rows"), pl.col("tradestatus").n_unique().alias("distinct_statuses")
    ).filter(pl.col("rows") > 1)
    if auxiliary_duplicates.filter(pl.col("distinct_statuses") > 1).height:
        raise RuntimeError("Auxiliary qfq cache has conflicting trade statuses for duplicate keys")
    auxiliary = auxiliary.unique(subset=["date", "symbol"], keep="last")
    matched = absent.join(auxiliary, on=["date", "symbol"], how="left")
    status_counts = {
        str(row["tradestatus"]): int(row["len"])
        for row in matched.group_by("tradestatus").len().iter_rows(named=True)
    }
    absent_by_symbol = (
        absent.group_by("symbol").len().sort(["len", "symbol"], descending=[True, False])
    )
    absent_by_year = {
        year: count
        for year, count in sorted(Counter(str(value)[:4] for value in absent["date"].to_list()).items())
    }
    absent_by_date = (
        absent.group_by("date").len().sort(["len", "date"], descending=[True, False]).head(30)
    )
    unresolved_by_symbol = (
        matched.filter(pl.col("tradestatus").is_null())
        .group_by("symbol").len().sort(["len", "symbol"], descending=[True, False])
    )
    output = {
        "round": "R305",
        "classification": "posthoc_read_only_member_key_coverage_and_auxiliary_trade_status_diagnostic; not alpha or return evidence",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash,
        "audit_script_sha256": sha(Path(__file__).resolve()),
        "input_sha256": before_hashes,
        "input_count": len(before_hashes),
        "columns_read": {
            "membership": ["query_date", "symbol"],
            "canonical_daily_partitions": ["date", "symbol"],
            "auxiliary_bootstrap": ["date", "symbol", "tradestatus", "price_mode"],
        },
        "membership": {
            "rows": membership.height,
            "query_dates": membership["query_date"].n_unique(),
            "symbols": membership["symbol"].n_unique(),
            "duplicate_date_symbol_keys": duplicate_membership_keys.height,
            "start": str(membership["query_date"].min()),
            "end": str(membership["query_date"].max()),
        },
        "canonical_daily": {
            "rows_in_membership_date_range": canonical.height,
            "dates": canonical["date"].n_unique(),
            "duplicate_date_symbol_keys": duplicates.height,
        },
        "exact_key_coverage": {
            "member_date_symbol_keys": member_keys.height,
            "canonical_present": member_keys.height - absent.height,
            "canonical_absent": absent.height,
            "coverage_ratio": round((member_keys.height - absent.height) / member_keys.height, 8),
            "absent_by_symbol": compact_rows(absent_by_symbol),
            "absent_by_year": absent_by_year,
            "most_affected_dates_top_30": compact_rows(absent_by_date),
        },
        "auxiliary_qfq_match_for_canonical_absence": {
            "matched_rows": matched.filter(pl.col("tradestatus").is_not_null()).height,
            "unmatched_rows": absent.height - matched.filter(pl.col("tradestatus").is_not_null()).height,
            "duplicate_keys_in_auxiliary_before_deduplication": auxiliary_duplicates.height,
            "trade_status_counts": status_counts,
            "interpretation": {
                "1": "auxiliary source reports tradable row; this does not prove why it is absent from canonical partitions",
                "0": "auxiliary source reports non-trading status; not independently verified as suspension",
                "null": "unresolved; no exact auxiliary qfq key match",
            },
            "unmatched_by_symbol": compact_rows(unresolved_by_symbol),
        },
        "a14_missing_event_key_check": {
            "date": "2020-06-10",
            "symbol": "600745.SH",
            "outside_R78_membership_period": True,
            "within_R305_audit_scope": False,
            "status": "not inspected in this round",
        },
        "limits": [
            "R78 membership is a local historical snapshot series, not independently certified as an official CSI daily source in this audit.",
            "The auxiliary bootstrap only explains exact matching keys and its own trade_status labels; unmatched rows remain unresolved.",
            "No OHLCV values, adjusted prices, returns, forward outcomes, or performance fields were read.",
            "No imputation, backtest, alpha scan, provider/builder call, or production write was performed.",
            "This posthoc diagnostic does not revise R304-A14's NO-GO or any existing candidate status.",
        ],
    }

    after_hashes = {str(path.relative_to(ROOT)): sha(path) for path in input_paths}
    if before_hashes != after_hashes:
        raise RuntimeError("An input changed during R305 audit; result not written")
    raw = (json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "member_date_symbol_keys": member_keys.height,
        "canonical_absent": absent.height,
        "auxiliary_trade_status_counts": status_counts,
        "auxiliary_unmatched": absent.height - matched.filter(pl.col("tradestatus").is_not_null()).height,
        "inputs_unchanged": before_hashes == after_hashes,
        "output_sha256": hashlib.sha256(raw).hexdigest(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
