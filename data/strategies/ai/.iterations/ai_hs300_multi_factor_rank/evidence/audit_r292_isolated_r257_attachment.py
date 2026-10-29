#!/usr/bin/env python3
"""Rebuild one R257 date in scratch and verify production exact-date attachment."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime, date
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

TARGET_DATE = "2026-10-19"
BASE_FIELDS = [
    "industry_mom_20d",
    "industry_mom_60d",
    "industry_rps_20d",
    "industry_breadth_20d",
    "stock_rel_industry_20d",
]
RESULT_PATH = EVIDENCE / "r292_isolated_attachment_audit.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}" + chr(10))
    return value


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load production module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_frozen_inputs(prereg: dict) -> list[dict]:
    manifest_path = EVIDENCE / Path(prereg["frozen_scope"]["input_manifest"]).name
    if sha256(manifest_path) != prereg["frozen_scope"]["input_manifest_sha256"]:
        raise RuntimeError("R292 frozen manifest checksum mismatch")
    rows = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    for row in rows:
        path = ROOT / row["path"]
        stat = path.stat()
        if (
            stat.st_size != row["bytes"]
            or stat.st_mtime_ns != row["mtime_ns"]
            or sha256(path) != row["sha256"]
        ):
            raise RuntimeError(f"Frozen R292 input changed: {row['path']}")
    return rows


def main() -> int:
    if RESULT_PATH.exists() or RESULT_PATH.with_name(RESULT_PATH.name + ".sha256").exists():
        raise RuntimeError("R292 audit output exists; refusing to overwrite")
    prereg_path = EVIDENCE / "r292_preregistration.json"
    prereg_bytes = prereg_path.read_bytes()
    prereg = json.loads(prereg_bytes)
    prereg_hash = hashlib.sha256(prereg_bytes).hexdigest()
    prereg_sidecar = prereg_path.with_name(prereg_path.name + ".sha256")
    if prereg_hash != prereg_sidecar.read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R292 preregistration checksum mismatch")
    if prereg.get("round") != "R292" or prereg.get("status") != "frozen_before_results":
        raise RuntimeError("R292 preregistration is not frozen")
    frozen_inputs = verify_frozen_inputs(prereg)

    base = load_module(ROOT / "scripts" / "build_alt_factor_data.py", "build_alt_factor_data_r292")
    r257 = load_module(
        ROOT / "scripts" / "build_r257_quality_relative_strength_factor.py",
        "build_r257_quality_relative_strength_r292",
    )
    from app.factors.ext_factors import attach_ext_columns, ext_column_name

    base_partition = (
        DATA / "ext_data" / "alt_hs300" / "timeseries"
        / f"date={TARGET_DATE}" / "part.parquet"
    )
    market_root = DATA / "kline_daily_enriched"
    market_paths = sorted(market_root.glob("date=*/part.parquet"))
    if not market_paths:
        raise RuntimeError("No frozen enriched K-line partitions found")
    stored_base = pl.read_parquet(base_partition)
    symbols = sorted(stored_base["symbol"].cast(pl.String).unique().to_list())
    dates = (
        pl.scan_parquet([str(path) for path in market_paths])
        .select("date")
        .unique()
        .collect()
        .sort("date")["date"]
        .to_list()
    )
    industry = base._industry_daily(dates, symbols)
    industry_day = industry.filter(pl.col("date") == date.fromisoformat(TARGET_DATE))
    if industry_day.is_empty():
        raise RuntimeError("Production industry function returned no target-date rows")

    scratch_root = None
    with tempfile.TemporaryDirectory(prefix="r292_r257_isolated_") as scratch_name:
        scratch = Path(scratch_name)
        scratch_root = "temporary directory; removed at process exit"
        for relative in (
            "research/r78_daily_membership/daily_snapshots.parquet",
            "research/r157_membership/monthly_snapshots.parquet",
            "ext_data/ext_hy_ths/part.parquet",
        ):
            target = scratch / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DATA / relative, target)
        for config_id in ("alt_hs300", "alt_hs300_minrank", "alt_hs300_qrs"):
            source = DATA / "ext_data" / config_id
            target = scratch / "ext_data" / config_id
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / "config.json", target / "config.json")

        base_columns = [column for column in BASE_FIELDS if column in stored_base.columns]
        rebuilt_base = (
            stored_base.drop(base_columns)
            .join(
                industry_day.select(["symbol", *BASE_FIELDS]),
                on="symbol",
                how="left",
            )
        )
        base_target = scratch / "ext_data" / "alt_hs300" / "timeseries" / f"date={TARGET_DATE}"
        base_target.mkdir(parents=True, exist_ok=True)
        rebuilt_base.write_parquet(base_target / "part.parquet")

        quality_source = (
            DATA / "ext_data" / "alt_hs300_minrank" / "timeseries"
            / f"date={TARGET_DATE}" / "part.parquet"
        )
        quality_target = (
            scratch / "ext_data" / "alt_hs300_minrank" / "timeseries"
            / f"date={TARGET_DATE}"
        )
        quality_target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(quality_source, quality_target / "part.parquet")

        # The production composite builder reads only these isolated components.
        base.DATA = scratch
        r257.DATA = scratch
        composite = r257._factor_daily(base)
        composite_valid = composite.filter(pl.col(r257.FIELD).is_not_null())
        if composite_valid.height == 0:
            raise RuntimeError("Production R257 composite calculation returned no values")
        composite_target = (
            scratch / "ext_data" / "alt_hs300_qrs" / "timeseries"
            / f"date={TARGET_DATE}"
        )
        composite_target.mkdir(parents=True, exist_ok=True)
        composite.drop("date").write_parquet(composite_target / "part.parquet")

        market_path = market_root / f"date={TARGET_DATE}" / "part.parquet"
        market = pl.read_parquet(market_path, columns=["symbol", "date"])
        attached = attach_ext_columns(market, include_snapshot=False, data_dir=scratch)
        attached_name = ext_column_name("alt_hs300_qrs", r257.FIELD)
        if attached_name not in attached.columns:
            raise RuntimeError(f"Production attachment omitted {attached_name}")
        attached_values = attached.select(["symbol", attached_name]).unique(subset=["symbol"])
        expected = composite.select(["symbol", r257.FIELD])
        joined = expected.join(attached_values, on="symbol", how="left")
        compared = joined.filter(
            pl.col(r257.FIELD).is_not_null()
            & pl.col(attached_name).is_not_null()
        )
        equal = compared.filter(pl.col(r257.FIELD) == pl.col(attached_name)).height
        attached_nonnull = attached.filter(pl.col(attached_name).is_not_null())["symbol"].n_unique()
        criteria = {
            "scratch_composite_nonnull_at_least_200": composite_valid.height >= 200,
            "production_attachment_nonnull_at_least_200": attached_nonnull >= 200,
            "all_nonnull_scratch_values_match_attachment": equal == composite_valid.height,
        }
        if not all(criteria.values()):
            status = "isolated_rebuild_or_attachment_failed"
        else:
            status = "isolated_rebuild_and_system_attachment_pass"

        result = {
            "round": "R292",
            "classification": "isolated_production_factor_rebuild_and_system_attachment_audit",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "target_date": TARGET_DATE,
            "universe": {
                "symbols_in_stored_base_partition": len(symbols),
                "production_industry_rows": industry_day.height,
                "market_rows_attached": market.height,
            },
            "coverage": {
                "production_industry_relative_strength_nonnull": industry_day[
                    "stock_rel_industry_20d"
                ].is_not_null().sum(),
                "stored_base_relative_strength_nonnull": stored_base[
                    "stock_rel_industry_20d"
                ].is_not_null().sum(),
                "scratch_composite_nonnull": composite_valid.height,
                "production_attachment_nonnull_symbols": attached_nonnull,
                "non_null_values_compared": compared.height,
                "equal_values": equal,
            },
            "criteria": criteria,
            "execution_limits": {
                "scratch_only_rebuild": True,
                "registered_market_or_ext_data_written": False,
                "provider_called": False,
                "builder_main_called": False,
                "backtest_or_matching_run": False,
                "returns_or_trades_read": False,
                "strategy_or_production_state_changed": False,
                "scratch_directory": scratch_root,
                "frozen_input_files_verified_before": len(frozen_inputs),
            },
            "preregistration_sha256": prereg_hash,
            "input_manifest_sha256": prereg["frozen_scope"]["input_manifest_sha256"],
        }

    verified_after = verify_frozen_inputs(prereg)
    if verified_after != frozen_inputs:
        raise RuntimeError("Frozen R292 inputs changed during audit")
    result["execution_limits"]["all_frozen_inputs_unchanged_after"] = True
    result["execution_limits"]["frozen_input_files_verified_after"] = len(verified_after)
    payload = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)).encode()
    write_once(RESULT_PATH, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if status == "isolated_rebuild_and_system_attachment_pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
