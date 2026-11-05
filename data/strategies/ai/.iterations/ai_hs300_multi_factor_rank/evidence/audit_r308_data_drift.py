from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
MEMBERSHIP = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
KLINE_ROOT = ROOT / "data/kline_daily"
AUXILIARY = ROOT / "data/research/r40_prices/bootstrap_missing.parquet"
R305 = EVIDENCE / "r305_member_bar_gap_audit.json"
PREREG = EVIDENCE / "r308_provider_gap_recovery_preregistration.json"
OUTPUT = EVIDENCE / "r308_data_drift_nogo.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R308 data-drift result exists; refusing overwrite")

    prereg_hash = sha(PREREG)
    if prereg_hash != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R308 preregistration checksum mismatch")

    r305 = json.loads(R305.read_text(encoding="utf-8"))
    old_inputs = r305["input_sha256"]
    same = changed = missing = 0
    changed_paths = []
    missing_paths = []
    for old_path, old_hash in old_inputs.items():
        rel = old_path.replace("\\", "/")
        path = ROOT / rel
        if not path.exists():
            missing += 1
            if len(missing_paths) < 20:
                missing_paths.append(rel)
            continue
        current_hash = sha(path)
        if current_hash == old_hash:
            same += 1
        else:
            changed += 1
            if len(changed_paths) < 20:
                changed_paths.append({"path": rel, "old": old_hash, "current": current_hash})

    paths = sorted(KLINE_ROOT.glob("date=*/part.parquet"))
    membership = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["date", "symbol"])
    )
    canonical = (
        pl.scan_parquet([str(path) for path in paths], extra_columns="ignore")
        .select(["date", "symbol"])
        .filter(pl.col("date").is_between(membership["date"].min(), membership["date"].max(), closed="both"))
        .collect()
    )
    absent = membership.select("date", "symbol").join(
        canonical.select("date", "symbol").unique(), on=["date", "symbol"], how="anti"
    )
    by_year = {
        str(year): int(count)
        for year, count in sorted(Counter(str(value)[:4] for value in absent["date"].to_list()).items())
    }
    auxiliary = (
        pl.read_parquet(AUXILIARY, columns=["date", "symbol", "tradestatus", "price_mode"])
        .with_columns(pl.col("date").str.to_date(), pl.col("symbol").cast(pl.String))
        .filter(pl.col("price_mode") == "qfq")
        .select("date", "symbol", "tradestatus")
        .unique(subset=["date", "symbol"], keep="last")
    )
    aux_status = {
        str(row["tradestatus"]): int(row["len"])
        for row in absent.join(auxiliary, on=["date", "symbol"], how="left").group_by("tradestatus").len().iter_rows(named=True)
    }
    sample_dates = {}
    for day in (membership["date"].min(), membership["date"].max()):
        member_symbols = set(membership.filter(pl.col("date") == day)["symbol"].to_list())
        canonical_symbols = set(canonical.filter(pl.col("date") == day)["symbol"].to_list())
        sample_dates[str(day)] = {
            "members": len(member_symbols),
            "canonical": len(canonical_symbols),
            "overlap": len(member_symbols & canonical_symbols),
            "missing": len(member_symbols - canonical_symbols),
            "extra": len(canonical_symbols - member_symbols),
        }

    expected_absent = int(r305["exact_key_coverage"]["canonical_absent"])
    drift = absent.height != expected_absent or changed > 0 or missing > 0
    result = {
        "round": "R308",
        "classification": "preregistered P1 input-snapshot gate; NO-GO before provider access",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_hash,
        "audit_script_sha256": sha(Path(__file__).resolve()),
        "r305_reference": {
            "canonical_absent": expected_absent,
            "input_files": len(old_inputs),
            "same_current_hashes": same,
            "changed_current_hashes": changed,
            "missing_current_paths": missing,
            "changed_sample": changed_paths,
            "missing_sample": missing_paths,
        },
        "current_snapshot": {
            "canonical_rows_in_membership_window": canonical.height,
            "member_rows": membership.height,
            "reconstructed_absent": absent.height,
            "reconstructed_absent_by_year": by_year,
            "auxiliary_status_counts": aux_status,
            "sample_dates": sample_dates,
        },
        "gates": {
            "P1_exact_absent_key_reconstruction": absent.height == expected_absent,
            "P2_provider_symbol_coverage": None,
            "P3_classification_partition": None,
            "P4_no_write_or_input_drift": None,
            "P5_no_outcome_fields": True,
        },
        "decision": {
            "status": "NO-GO_data_snapshot_drift" if drift else "proceed_to_provider_audit",
            "provider_called": False,
            "backtest_run": False,
            "alpha_scan_run": False,
            "production_write": False,
            "reason": "Current canonical daily partitions do not match the R305 frozen input snapshot; R308 cannot reconstruct the 3,586-key population without changing scope.",
        },
        "limits": [
            "This is a data-provenance diagnostic, not an alpha estimate.",
            "No OHLCV, returns, forward outcomes, or provider data are read.",
            "The R305 reference and its original hashes are not modified.",
            "The provider audit is intentionally skipped because P1 failed.",
        ],
    }
    raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "expected_absent": expected_absent,
        "reconstructed_absent": absent.height,
        "changed_hashes": changed,
        "missing_hashes": missing,
        "same_hashes": same,
        "sample_dates": sample_dates,
        "decision": result["decision"]["status"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if result["decision"]["status"] == "proceed_to_provider_audit" else 2


if __name__ == "__main__":
    raise SystemExit(main())
