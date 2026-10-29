#!/usr/bin/env python3
"""Audit corrected R272 factor construction and historical-member coverage only."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
BUILD_EVIDENCE = EVIDENCE / "r272_factor_build_final.json"
PARTITION_MANIFEST = EVIDENCE / "r272_factor_build_final_partitions.json"
IMPLEMENTATION_LOCK = EVIDENCE / "r272_implementation_correction_final_v3.json"
OUT = EVIDENCE / "r272_corrected_factor_audit_v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load research helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if OUT.exists() or OUT.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 corrected factor audit already exists; refusing to overwrite")
    builder_path = ROOT / "scripts/build_r272_operating_margin_factor.py"
    runner_path = ROOT / "scripts/backtest_r272_operating_margin_improvement.py"
    builder = _load(builder_path, "builder_r272_factor_audit")
    runner = _load(runner_path, "runner_r272_factor_audit")
    lock, lock_hash, canonical_factor_id = builder._verify_implementation_lock()
    if not BUILD_EVIDENCE.exists() or not PARTITION_MANIFEST.exists():
        raise RuntimeError("corrected R272 build proof and partition manifest are required")
    build = json.loads(BUILD_EVIDENCE.read_text(encoding="utf-8"))
    manifest = json.loads(PARTITION_MANIFEST.read_text(encoding="utf-8"))
    build_sidecar = BUILD_EVIDENCE.with_suffix(".json.sha256")
    manifest_sidecar = PARTITION_MANIFEST.with_suffix(".json.sha256")
    if not build_sidecar.exists() or build_sidecar.read_text(encoding="ascii").split()[0] != _sha256(BUILD_EVIDENCE):
        raise RuntimeError("R272 corrected build evidence checksum mismatch")
    if not manifest_sidecar.exists() or manifest_sidecar.read_text(encoding="ascii").split()[0] != _sha256(PARTITION_MANIFEST):
        raise RuntimeError("R272 corrected partition manifest sidecar mismatch")
    if build.get("implementation_correction_lock_sha256") != lock_hash:
        raise RuntimeError("R272 build proof references a different implementation lock")
    if build.get("factor_id") != canonical_factor_id:
        raise RuntimeError("R272 build factor id does not match the system registry")
    if _sha256(PARTITION_MANIFEST) != build.get("partition_manifest", {}).get("sha256"):
        raise RuntimeError("R272 corrected partition manifest checksum mismatch")
    actual_partitions = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in sorted((ROOT / "data/ext_data/alt_hs300_opm/timeseries").glob("date=*/part.parquet"))
    }
    if manifest != actual_partitions:
        raise RuntimeError("R272 corrected partitions differ from their build-time manifest")

    probe = _load(ROOT / "scripts/probe_r272_operating_margin_improvement.py", "probe_r272_factor_audit")
    r221 = _load(ROOT / "scripts/backtest_r221_sga_nonfin_quality.py", "r221_r272_factor_audit")
    r229 = _load(ROOT / "scripts/backtest_r229_ocf_margin.py", "r229_r272_factor_audit")
    membership, universe = runner._membership(r221)
    calendar = r229._benchmark_dates(runner.START, runner.END)
    symbols = sorted(membership["symbol"].unique().to_list())
    factor_all = runner._factor_rows()
    factor = factor_all.filter(
        __import__("polars").col("date").is_between(runner.START, runner.END, closed="both")
    )
    coverage = runner._coverage(membership, factor, calendar)
    pit = runner._pit_audit(
        factor_all, builder, builder._load_base_builder(), calendar, symbols, membership
    )
    independent_members = probe._membership(pl.DataFrame({"date": calendar}))
    independent_records = probe._financial_factor()
    independent_factor = probe._attach_candidate(independent_members, independent_records).rename(
        {probe.FACTOR: runner.FACTOR_COLUMN}
    )
    member_keys = runner._daily_membership(membership, calendar).filter(
        pl.col("date").is_between(runner.START, runner.END, closed="both")
    ).select(["date", "symbol"])
    eligible_keys = member_keys.join(factor, on=["date", "symbol"], how="left")
    values = eligible_keys.drop_nulls([runner.FACTOR_COLUMN])[runner.FACTOR_COLUMN]
    written_members = factor.filter(
        pl.col("date").is_between(runner.START, runner.END, closed="both")
    ).join(member_keys, on=["date", "symbol"], how="semi")
    independent_members = independent_factor.join(
        member_keys, on=["date", "symbol"], how="semi"
    )
    independent_join = independent_members.join(
        written_members, on=["date", "symbol"], how="full", suffix="_written"
    )
    independent_value = runner.FACTOR_COLUMN + "_written"
    independent_value_pairs = independent_join.filter(
        pl.col(runner.FACTOR_COLUMN).is_not_null() & pl.col(independent_value).is_not_null()
    )
    independent_delta = (
        independent_value_pairs.select(
            (pl.col(runner.FACTOR_COLUMN) - pl.col(independent_value)).abs().max()
        ).item()
        if independent_value_pairs.height
        else None
    )
    independent_pit = {
        "independent_member_rows": independent_members.height,
        "written_member_rows": written_members.height,
        "missing_written_keys": independent_members.join(
            written_members, on=["date", "symbol"], how="anti"
        ).height,
        "extra_written_keys": written_members.join(
            independent_members, on=["date", "symbol"], how="anti"
        ).height,
        "max_absolute_value_difference": float(independent_delta)
        if independent_delta is not None
        else None,
        "all_checks_pass": (
            independent_members.height == written_members.height
            and independent_members.join(
                written_members, on=["date", "symbol"], how="anti"
            ).is_empty()
            and written_members.join(
                independent_members, on=["date", "symbol"], how="anti"
            ).is_empty()
            and independent_delta is not None
            and independent_delta <= 1e-12
        ),
    }
    eligible_keys = runner._daily_membership(membership, calendar).join(
        factor, on=["date", "symbol"], how="left"
    )
    written_values = factor_all[runner.FACTOR_COLUMN]
    date_key_duplicates = factor_all.height - factor_all.select(["symbol", "date"]).unique().height
    coverage_pass = (
        coverage["full_coverage"] >= 0.99
        and coverage["recent_252_trading_day_coverage"] >= 0.99
        and coverage["first_valid_date"] <= str(runner.START)
    )
    checks = {
        "lock_chain_valid": lock_hash == _sha256(IMPLEMENTATION_LOCK),
        "partition_inventory_matches": manifest == actual_partitions,
        "partition_count_matches_build_total": len(actual_partitions) == build["partitions"],
        "factor_row_count_matches_build_total": factor_all.height == build["factor_rows"],
        "daily_member_coverage_gates_pass": coverage_pass,
        "same_member_key_pit_rebuild_passes": pit["all_checks_pass"],
        "independent_feasibility_probe_matches_written_factor": independent_pit["all_checks_pass"],
        "no_duplicate_factor_symbol_date_keys": date_key_duplicates == 0,
        "no_null_or_nonfinite_written_factor_values": written_values.null_count() == 0
        and written_values.is_finite().all(),
    }
    payload = {
        "round": "R272",
        "status": "corrected_factor_audited_before_panel_registration_and_outcome_analysis",
        "audited_at": datetime.now(UTC).isoformat(),
        "implementation_correction_lock": {
            "path": IMPLEMENTATION_LOCK.relative_to(ROOT).as_posix(),
            "sha256": lock_hash,
        },
        "build_evidence": {
            "path": BUILD_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha256(BUILD_EVIDENCE),
            "rows": build["factor_rows"],
            "symbols": build["valid_symbols"],
            "partitions": build["partitions"],
        },
        "partition_manifest": {
            "path": PARTITION_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(PARTITION_MANIFEST),
            "partition_count": len(actual_partitions),
        },
        "canonical_factor_id": canonical_factor_id,
        "window": [str(runner.START), str(runner.END)],
        "historical_universe": universe,
        "daily_member_coverage": coverage,
        "same_member_key_pit_audit": pit,
        "independent_feasibility_probe_audit": independent_pit,
        "eligible_member_value_distribution": {
            "count": len(values),
            "min": float(values.min()),
            "median": float(values.median()),
            "max": float(values.max()),
            "null_count": values.null_count(),
            "nonfinite_count": int((~values.is_finite()).sum()),
        },
        "source_sha256": {
            name: item["current_sha256"] for name, item in lock["frozen_inputs"].items()
        },
        "audit_source_sha256": _sha256(Path(__file__).resolve()),
        "supersedes_preliminary_audit": {
            "path": (EVIDENCE / "r272_corrected_factor_audit.json").relative_to(ROOT).as_posix(),
            "sha256": _sha256(EVIDENCE / "r272_corrected_factor_audit.json"),
            "reason": "preliminary audit mislabeled a partition-count assertion as a row-count check and did not independently verify written values against the feasibility probe",
            "disposition": "preserved_as_superseded_preliminary_audit",
        },
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "scope": {
            "forward_returns_read": False,
            "trade_records_read": False,
            "backtest_run": False,
            "panel_reload_run": False,
            "production_status": "pause_production",
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    OUT.with_suffix(".json.sha256").write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"audit": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "checks": checks, "coverage": coverage, "pit": pit}, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
