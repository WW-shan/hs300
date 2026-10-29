#!/usr/bin/env python3
"""Build the R272 PIT operating-margin year-over-year improvement factor."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.fundamentals import _statutory_deadline
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.fs_utils import atomic_write_parquet

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = DATA / "strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
CONFIG_ID = "alt_hs300_opm"
FIELD = "operating_margin_yoy_change_q"
MAX_AGE_DAYS = 180
INCOME = DATA / "financials/income/part.parquet"
IMPLEMENTATION_LOCK = EVIDENCE / "r272_implementation_correction_final_v3.json"
BUILD_EVIDENCE = EVIDENCE / "r272_factor_build_final.json"
ARCHIVE_EVIDENCE = EVIDENCE / "r272_superseded_factor_archive_final.json"
ARCHIVE_ROOT = DATA / "research/r272_superseded_factor_build_final"
PARTITION_MANIFEST = EVIDENCE / "r272_factor_build_final_partitions.json"
ARCHIVED_PARTITION_MANIFEST = EVIDENCE / "r272_superseded_factor_archive_final_partitions.json"
OLD_BUILD_EVIDENCE = EVIDENCE / "r272_factor_build.json"
INTERIM_BUILD_EVIDENCE = EVIDENCE / "r272_factor_build_corrected.json"
INTERIM_PARTITION_MANIFEST = EVIDENCE / "r272_factor_build_corrected_partitions.json"
STRATEGY_PATHS = {
    "candidate": DATA / "strategies/ai/ai_hs300_operating_margin_improvement_quality_h20_sl20.py",
    "control": DATA / "strategies/ai/ai_hs300_operating_margin_improvement_quality_control.py",
}
RUNNER_PATH = ROOT / "scripts/backtest_r272_operating_margin_improvement.py"
TEST_PATH = ROOT / "backend/tests/test_r272_operating_margin_factor.py"
FEASIBILITY_PREREG = EVIDENCE / "r272_feasibility_preregistration.json"
FEASIBILITY_PROBE = ROOT / "scripts/probe_r272_operating_margin_improvement.py"
FEASIBILITY_REGISTRAR = ROOT / "scripts/preregister_r272_feasibility.py"
PREREG_REGISTRAR = ROOT / "scripts/preregister_r272.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_implementation_lock() -> tuple[dict, str, str]:
    if not IMPLEMENTATION_LOCK.exists():
        raise RuntimeError("R272 implementation correction lock is missing")
    sidecar = IMPLEMENTATION_LOCK.with_suffix(".json.sha256")
    if not sidecar.exists():
        raise RuntimeError("R272 implementation correction lock checksum is missing")
    lock_hash = _sha256(IMPLEMENTATION_LOCK)
    expected_sidecar = f"{lock_hash}  {IMPLEMENTATION_LOCK.name}"
    if sidecar.read_text(encoding="ascii").strip() != expected_sidecar:
        raise RuntimeError("R272 implementation correction lock checksum mismatch")

    prereg_path = EVIDENCE / "r272_preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    feasibility_prereg = json.loads(FEASIBILITY_PREREG.read_text(encoding="utf-8"))
    lock = json.loads(IMPLEMENTATION_LOCK.read_text(encoding="utf-8"))
    old_build_hash = _sha256(OLD_BUILD_EVIDENCE)
    interim_build_hash = _sha256(INTERIM_BUILD_EVIDENCE)
    interim_partition_manifest_hash = _sha256(INTERIM_PARTITION_MANIFEST)
    expected_sources = {
        "builder": _sha256(Path(__file__).resolve()),
        "runner": _sha256(RUNNER_PATH),
        "candidate_strategy": _sha256(STRATEGY_PATHS["candidate"]),
        "control_strategy": _sha256(STRATEGY_PATHS["control"]),
        "test": _sha256(TEST_PATH),
        "feasibility_probe": _sha256(FEASIBILITY_PROBE),
        "feasibility_registrar": _sha256(FEASIBILITY_REGISTRAR),
        "preregistration_registrar": _sha256(PREREG_REGISTRAR),
    }
    from app.factors import ext_factors

    canonical_factor_id = ext_factors.ext_column_name(CONFIG_ID, FIELD)
    source_chain = lock.get("source_chain", {})
    registered = lock.get("registered_source_hashes", {})
    factor_identity = lock.get("factor_identity", {})
    corrected_hashes = lock.get("corrected_source_hashes", {})
    feasibility_evidence = json.loads(
        (EVIDENCE / "r272_data_feasibility.json").read_text(encoding="utf-8")
    )
    feasibility_sidecar = (EVIDENCE / "r272_data_feasibility.json.sha256").read_text(encoding="ascii").split()
    checks = {
        "preregistration_sha256": source_chain.get("original_preregistration", {}).get("sha256") == _sha256(prereg_path),
        "feasibility_sha256": source_chain.get("feasibility_evidence", {}).get("sha256")
        == _sha256(EVIDENCE / "r272_data_feasibility.json"),
        "feasibility_sidecar": bool(feasibility_sidecar)
        and feasibility_sidecar[0] == _sha256(EVIDENCE / "r272_data_feasibility.json"),
        "feasibility_preregistration_link": feasibility_evidence.get("preregistration_sha256")
        == _sha256(FEASIBILITY_PREREG)
        and feasibility_evidence.get("probe_sha256") == registered.get("feasibility_probe"),
        "feasibility_preregistration_probe_link": feasibility_prereg.get("probe_sha256")
        == registered.get("feasibility_probe"),
        "feasibility_inputs_unchanged": all(
            item.get("unchanged") is True
            and item.get("registered_sha256") == item.get("current_sha256")
            for item in lock.get("frozen_inputs", {}).values()
        ),
        "correction_lock_writer_hash": corrected_hashes.get("correction_lock_writer")
        == _sha256(ROOT / "scripts/lock_r272_implementation_correction.py"),
        "old_build_evidence_sha256": source_chain.get("previous_build_evidence", {}).get("sha256") == old_build_hash,
        "interim_build_evidence_sha256": source_chain.get("superseded_interim_build", {}).get("sha256") == interim_build_hash,
        "interim_partition_manifest_sha256": source_chain.get("superseded_interim_build", {}).get("partition_manifest_sha256") == interim_partition_manifest_hash,
        "registered_builder_sha256": prereg.get("builder_sha256") == registered.get("builder"),
        "registered_runner_sha256": prereg.get("runner_sha256") == registered.get("runner"),
        "registered_preregistrar_sha256": prereg.get("preregistration_script_sha256") == registered.get("preregistration_registrar"),
        "feasibility_probe_sha256": feasibility_prereg.get("probe_sha256")
        == registered.get("feasibility_probe"),
        "feasibility_registrar_sha256": feasibility_prereg.get("preregistration_script_sha256")
        == registered.get("feasibility_registrar"),
        "corrected_source_sha256": all(corrected_hashes.get(name) == digest for name, digest in expected_sources.items()),
        "locked_source_inventory": all(
            _sha256(ROOT / item["path"]) == item["sha256"]
            for item in lock.get("source_inventory", {}).values()
        ),
        "lock_writer_source_hash": corrected_hashes.get("correction_lock_writer")
        == _sha256(ROOT / "scripts/lock_r272_implementation_correction.py"),
        "canonical_factor_id": factor_identity.get("canonical_factor_id_from_system_registry") == canonical_factor_id,
        "preregistered_factor_alias": prereg.get("factor", {}).get("id") == factor_identity.get("preregistered_alias"),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"R272 implementation correction lock failed checks: {failed}")
    return lock, lock_hash, canonical_factor_id


def _load_base_builder():
    path = ROOT / "scripts/build_alt_factor_data.py"
    spec = importlib.util.spec_from_file_location("r272_base_factor_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base factor builder: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _financial_panel() -> pl.DataFrame:
    income = (
        pl.read_parquet(
            INCOME,
            columns=["symbol", "period_end", "announce_date", "revenue", "operating_profit"],
        )
        .with_columns([
            pl.col("symbol").cast(pl.String),
            pl.col("period_end").cast(pl.Utf8).str.slice(0, 10).str.to_date(),
            pl.col("announce_date").cast(pl.Utf8).str.slice(0, 10).str.to_date().alias("_raw_announce"),
            pl.col("revenue").cast(pl.Float64),
            pl.col("operating_profit").cast(pl.Float64),
        ])
        .sort(["symbol", "period_end", "_raw_announce"])
        .unique(subset=["symbol", "period_end"], keep="last")
        .sort(["symbol", "period_end"])
        .with_columns([
            (pl.col("period_end").dt.year() * 4 + pl.col("period_end").dt.quarter()).alias("_period_index"),
            pl.col("period_end").map_elements(_statutory_deadline, return_dtype=pl.Date)
                .alias("_deadline"),
        ])
        .with_columns(
            pl.when(
                (pl.col("_raw_announce") > pl.col("period_end"))
                & (pl.col("_raw_announce") <= pl.col("_deadline"))
            )
            .then(pl.col("_raw_announce"))
            .otherwise(pl.col("_deadline").dt.offset_by("3d"))
            .alias("_pit")
        )
        .with_columns([
            pl.col("_period_index").shift(1).over("symbol").alias("_prev_index"),
            pl.col("revenue").shift(1).over("symbol").alias("_prev_revenue"),
            pl.col("operating_profit").shift(1).over("symbol").alias("_prev_operating_profit"),
            pl.col("_pit").shift(1).over("symbol").alias("_prev_pit"),
        ])
        .with_columns((pl.col("_period_index") - pl.col("_prev_index")).alias("_period_gap"))
        .with_columns([
            pl.when(pl.col("period_end").dt.quarter() == 1)
                .then(pl.col("revenue"))
                .when((pl.col("_period_gap") == 1) & pl.col("_prev_index").is_not_null())
                .then(pl.col("revenue") - pl.col("_prev_revenue"))
                .otherwise(None).alias("_q_revenue"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
                .then(pl.col("operating_profit"))
                .when((pl.col("_period_gap") == 1) & pl.col("_prev_index").is_not_null())
                .then(pl.col("operating_profit") - pl.col("_prev_operating_profit"))
                .otherwise(None).alias("_q_operating_profit"),
            pl.when(pl.col("period_end").dt.quarter() == 1)
                .then(pl.col("_pit"))
                .when((pl.col("_period_gap") == 1) & pl.col("_prev_index").is_not_null())
                .then(pl.max_horizontal("_pit", "_prev_pit"))
                .otherwise(None).alias("_q_available"),
        ])
        .with_columns(
            pl.when((pl.col("_q_revenue") > 0) & pl.col("_q_operating_profit").is_not_null())
                .then(pl.col("_q_operating_profit") / pl.col("_q_revenue"))
                .otherwise(None).alias("_q_margin")
        )
        .select(["symbol", "_period_index", "_q_margin", "_q_available"])
    )
    prior = income.select([
        "symbol",
        pl.col("_period_index").alias("_prior_index"),
        pl.col("_q_margin").alias("_yoy_margin"),
        pl.col("_q_available").alias("_yoy_available"),
    ])
    income = (
        income.with_columns((pl.col("_period_index") - 4).alias("_prior_index"))
        .join(prior, on=["symbol", "_prior_index"], how="left")
        .with_columns([
            pl.when(
                pl.col("_q_available").is_not_null()
                & pl.col("_yoy_available").is_not_null()
                & pl.col("_q_margin").is_not_null()
                & pl.col("_yoy_margin").is_not_null()
            )
            .then(pl.col("_q_margin") - pl.col("_yoy_margin"))
            .otherwise(None).alias(FIELD),
            pl.when(pl.col("_q_available").is_not_null() & pl.col("_yoy_available").is_not_null())
            .then(pl.max_horizontal("_q_available", "_yoy_available"))
            .otherwise(None).alias("_factor_available"),
        ])
        .filter(pl.col(FIELD).is_finite() & pl.col("_factor_available").is_not_null())
        .with_columns((pl.col("_factor_available") + pl.duration(days=1)).alias("effective_date"))
        .select(["symbol", "_period_index", "effective_date", FIELD])
        # unique(keep="last") does not preserve as-of key ordering.
        .sort(["symbol", "effective_date", "_period_index"])
        .unique(subset=["symbol", "effective_date"], keep="last")
        .sort(["symbol", "effective_date"])
        .drop("_period_index")
    )
    return income


def _calendar_frame(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    return builder._calendar_frame(dates, symbols).select(["symbol", pl.col("date").cast(pl.Date)])


def _factor_daily(builder, dates: list[date], symbols: list[str]) -> pl.DataFrame:
    financial = _financial_panel().sort(["symbol", "effective_date"])
    grid = _calendar_frame(builder, dates, symbols).sort(["symbol", "date"])
    joined = grid.join_asof(
        financial,
        left_on="date", right_on="effective_date", by="symbol",
        strategy="backward", check_sortedness=False,
    )
    return (
        joined
        .filter(
            pl.col("effective_date").is_not_null()
            & ((pl.col("date") - pl.col("effective_date")).dt.total_days() <= MAX_AGE_DAYS)
        )
        .select(["symbol", "date", FIELD])
    )


def _write_partitions(frame: pl.DataFrame) -> int:
    out = DATA / "ext_data" / CONFIG_ID / "timeseries"
    new_dates = {day.isoformat() for day in frame["date"].unique().to_list()}
    existing_paths = sorted(out.glob("date=*/part.parquet"))
    existing_dates = {path.parent.name.removeprefix("date=") for path in existing_paths}
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for part in frame.sort(["date", "symbol"]).partition_by("date", maintain_order=True):
        day = part["date"][0]
        target = out / f"date={day.isoformat()}" / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(part.drop("date"), target)
        count += 1
    for stale_date in sorted(existing_dates - new_dates):
        (out / f"date={stale_date}" / "part.parquet").unlink()
    return count


def _archive_previous_factor() -> dict:
    if ARCHIVE_EVIDENCE.exists() or ARCHIVE_EVIDENCE.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 superseded factor archive evidence already exists")
    config_path = DATA / "ext_data" / CONFIG_ID / "config.json"
    old_partitions = sorted((DATA / "ext_data" / CONFIG_ID / "timeseries").glob("date=*/part.parquet"))
    archived = []
    for source in old_partitions:
        relative = source.relative_to(DATA / "ext_data" / CONFIG_ID)
        target = ARCHIVE_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hash = _sha256(source)
        if _sha256(target) != source_hash:
            raise RuntimeError(f"R272 archived partition checksum mismatch: {relative}")
        archived.append({"path": source.relative_to(ROOT).as_posix(), "archive_path": target.relative_to(ROOT).as_posix(), "sha256": source_hash})
    archived_config = None
    if config_path.exists():
        target = ARCHIVE_ROOT / "config.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, target)
        archived_config = {"path": config_path.relative_to(ROOT).as_posix(), "archive_path": target.relative_to(ROOT).as_posix(), "sha256": _sha256(config_path)}
        if _sha256(target) != archived_config["sha256"]:
            raise RuntimeError("R272 archived config checksum mismatch")
    manifest_path = ARCHIVED_PARTITION_MANIFEST
    manifest_path.write_text(json.dumps(archived, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.with_suffix(".json.sha256").write_text(
        _sha256(manifest_path) + "  " + manifest_path.name + "\n", encoding="ascii"
    )
    payload = {
        "round": "R272",
        "status": "interim_corrected_factor_artifact_archived_before_final_rebuild",
        "previous_build_evidence": {
            "path": INTERIM_BUILD_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha256(INTERIM_BUILD_EVIDENCE),
        },
        "previous_partition_manifest": {
            "path": INTERIM_PARTITION_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(INTERIM_PARTITION_MANIFEST),
        },
        "archived_partition_count": len(archived),
        "archived_partition_bytes": sum((DATA / row["path"].removeprefix("data/")).stat().st_size for row in archived),
        "partition_manifest": {
            "path": manifest_path.relative_to(ROOT).as_posix(),
            "sha256": _sha256(manifest_path),
        },
        "config": archived_config,
    }
    ARCHIVE_EVIDENCE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ARCHIVE_EVIDENCE.with_suffix(".json.sha256").write_text(_sha256(ARCHIVE_EVIDENCE) + "  " + ARCHIVE_EVIDENCE.name + "\n", encoding="ascii")
    return payload


def _upsert_config() -> None:
    ExtConfigStore(DATA).upsert(ExtConfig(
        id=CONFIG_ID,
        label="HS300 operating margin YoY improvement",
        mode="timeseries",
        fields=[ExtField(FIELD, "float", "Quarterly operating-profit margin YoY change")],
        description=(
            "Research-only R272 factor: current single-quarter operating_profit/revenue minus same quarter one year ago."
            "Quarterization uses consecutive cumulative income-statement reports; statutory PIT fallback and 180-day expiry apply."
        ),
    ))


def main() -> int:
    prereg_path = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r272_preregistration.json"
    lock, lock_hash, _factor_id = _verify_implementation_lock()
    if BUILD_EVIDENCE.exists() or BUILD_EVIDENCE.with_suffix(".json.sha256").exists():
        raise RuntimeError("corrected R272 build evidence already exists; refusing to overwrite proof")
    base_builder = _load_base_builder()

    base_root = DATA / "ext_data" / "alt_hs300" / "timeseries"
    paths = sorted(base_root.glob("date=*/part.parquet"))
    if not paths:
        raise RuntimeError("base alt_hs300 factor time series is missing")
    dates = [date.fromisoformat(path.parent.name.removeprefix("date=")) for path in paths]
    symbols = (
        pl.scan_parquet([str(path) for path in paths], hive_partitioning=True)
        .select("symbol").unique().collect()["symbol"].sort().to_list()
    )
    financial = _financial_panel().sort(["symbol", "effective_date"])
    grid = _calendar_frame(base_builder, dates, symbols).sort(["symbol", "date"])
    factor = (
        grid.join_asof(financial, left_on="date", right_on="effective_date", by="symbol",
                       strategy="backward", check_sortedness=False)
        .filter(pl.col("effective_date").is_not_null()
                & ((pl.col("date") - pl.col("effective_date")).dt.total_days() <= MAX_AGE_DAYS))
        .select(["symbol", "date", FIELD])
    )
    if factor.is_empty():
        raise RuntimeError("corrected R272 factor build produced no rows")
    output_paths = [
        BUILD_EVIDENCE,
        BUILD_EVIDENCE.with_suffix(".json.sha256"),
        PARTITION_MANIFEST,
        PARTITION_MANIFEST.with_suffix(".json.sha256"),
        ARCHIVE_EVIDENCE,
        ARCHIVE_EVIDENCE.with_suffix(".json.sha256"),
        ARCHIVED_PARTITION_MANIFEST,
        ARCHIVED_PARTITION_MANIFEST.with_suffix(".json.sha256"),
    ]
    if any(path.exists() for path in output_paths) or ARCHIVE_ROOT.exists():
        raise RuntimeError("R272 corrected build or archive outputs already exist; refusing to overwrite evidence")
    archive = _archive_previous_factor()
    partitions = _write_partitions(factor)
    _upsert_config()
    from app.factors import ext_factors

    ext_factors.invalidate_ext_caches(DATA)
    ext_factors.ensure_synced(DATA)
    valid = factor.filter(pl.col(FIELD).is_not_null())
    partition_hashes = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in sorted((DATA / "ext_data" / CONFIG_ID / "timeseries").glob("date=*/part.parquet"))
    }
    if len(partition_hashes) != partitions:
        raise RuntimeError("corrected R272 partition inventory is inconsistent")
    PARTITION_MANIFEST.write_text(json.dumps(partition_hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    PARTITION_MANIFEST.with_suffix(".json.sha256").write_text(
        _sha256(PARTITION_MANIFEST) + "  " + PARTITION_MANIFEST.name + "\n", encoding="ascii"
    )
    payload = {
        "round": "R272",
        "status": "corrected_build_after_implementation_audit",
        "factor_id": ext_factors.ext_column_name(CONFIG_ID, FIELD),
        "implementation_correction_lock": IMPLEMENTATION_LOCK.relative_to(ROOT).as_posix(),
        "implementation_correction_lock_sha256": lock_hash,
        "builder_sha256": _sha256(Path(__file__).resolve()),
        "config_id": CONFIG_ID,
        "field": FIELD,
        "partitions": partitions,
        "partition_manifest": {
            "path": PARTITION_MANIFEST.relative_to(ROOT).as_posix(),
            "sha256": _sha256(PARTITION_MANIFEST),
        },
        "factor_rows": factor.height,
        "valid_rows": valid.height,
        "valid_symbols": valid["symbol"].n_unique(),
        "first_valid_date": str(valid["date"].min()),
        "last_valid_date": str(valid["date"].max()),
        "min": float(valid[FIELD].min()),
        "median": float(valid[FIELD].median()),
        "max": float(valid[FIELD].max()),
        "source_sha256": {"income": _sha256(INCOME)},
        "preregistration_sha256": _sha256(prereg_path),
        "previous_build_evidence": {
            "path": OLD_BUILD_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": lock["old_build_evidence_sha256"],
            "disposition": "retained_as_invalid_historical_build_record",
        },
        "superseded_factor_archive": {
            "path": ARCHIVE_EVIDENCE.relative_to(ROOT).as_posix(),
            "sha256": _sha256(ARCHIVE_EVIDENCE),
            "partition_count": archive["archived_partition_count"],
        },
    }
    evidence = BUILD_EVIDENCE
    evidence.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evidence.with_suffix(".json.sha256").write_text(_sha256(evidence) + "  " + evidence.name + "\n", encoding="ascii")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
