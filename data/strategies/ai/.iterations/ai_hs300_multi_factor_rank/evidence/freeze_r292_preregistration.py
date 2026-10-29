#!/usr/bin/env python3
"""Freeze R292 inputs and preregistration before the isolated rebuild audit."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
PARENT_MANIFEST = EVIDENCE / "r291_input_manifest.jsonl"
INPUT_MANIFEST = EVIDENCE / "r292_input_manifest.jsonl"
PREREGISTRATION = EVIDENCE / "r292_preregistration.json"
TARGET_DATE = "2026-10-19"

REQUIRED_PATHS = {
    "backend/app/factors/ext_factors.py",
    "backend/app/services/ext_data.py",
    "backend/app/services/fs_utils.py",
    "backend/app/services/industry_map.py",
    "scripts/build_alt_factor_data.py",
    "scripts/build_r253_min_rank_factor.py",
    "scripts/build_r257_quality_relative_strength_factor.py",
    "data/research/r78_daily_membership/daily_snapshots.parquet",
    "data/research/r157_membership/monthly_snapshots.parquet",
    "data/ext_data/ext_hy_ths/part.parquet",
    "data/ext_data/alt_hs300/config.json",
    f"data/ext_data/alt_hs300/timeseries/date={TARGET_DATE}/part.parquet",
    "data/ext_data/alt_hs300_minrank/config.json",
    f"data/ext_data/alt_hs300_minrank/timeseries/date={TARGET_DATE}/part.parquet",
    "data/ext_data/alt_hs300_qrs/config.json",
    f"data/ext_data/alt_hs300_qrs/timeseries/date={TARGET_DATE}/part.parquet",
    f"data/kline_daily_enriched/date={TARGET_DATE}/part.parquet",
}
PANEL_CONFIG = {
    "asset_type": "stock",
    "start": TARGET_DATE,
    "end": TARGET_DATE,
    "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "holding_days": 20,
    "max_positions": 10,
    "max_exposure_pct": 1.0,
    "initial_capital": 1000000.0,
    "commission_pct": 0.0006,
    "fees_pct": 0.0006,
    "stamp_tax_pct": 0.001,
    "slippage_bps": 15.0,
    "matching": "open_t+1",
    "minute_fill": False,
    "mode": "position",
    "position_sizing": "equal",
    "params": {},
    "overrides": {},
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = hashlib.sha256(payload).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}" + chr(10))
    return value


def frozen_record(relative: str) -> dict:
    path = ROOT / relative
    stat = path.stat()
    return {
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "path": relative,
        "sha256": digest(path),
    }


def main() -> int:
    for path in (INPUT_MANIFEST, INPUT_MANIFEST.with_name(INPUT_MANIFEST.name + ".sha256"),
                 PREREGISTRATION, PREREGISTRATION.with_name(PREREGISTRATION.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R292 freeze output exists; refusing to overwrite: {path.name}")

    parent_hash = digest(PARENT_MANIFEST)
    expected_parent_hash = "1ad023b966d60cd758a499d9fff45b8b5b6fab5061f1471a9e6a01fb4543d9e7"
    if parent_hash != expected_parent_hash:
        raise RuntimeError("R291 frozen input manifest checksum mismatch")
    parent_rows = [
        json.loads(line)
        for line in PARENT_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line
    ]
    parent_by_path = {row["path"]: row for row in parent_rows}
    market_paths = {
        row["path"] for row in parent_rows
        if row["path"].startswith("data/kline_daily_enriched/")
    }
    selected_paths = market_paths | REQUIRED_PATHS
    missing = sorted(path for path in REQUIRED_PATHS if path not in parent_by_path)
    records = [parent_by_path[path] for path in sorted(selected_paths & parent_by_path.keys())]
    recorded = {row["path"] for row in records}
    records.extend(frozen_record(path) for path in sorted(selected_paths - recorded))
    records.sort(key=lambda row: row["path"])

    manifest_payload = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + chr(10)
        for row in records
    ).encode("utf-8")
    manifest_hash = write_once(INPUT_MANIFEST, manifest_payload)
    preregistration = {
        "round": "R292",
        "status": "frozen_before_results",
        "classification": "isolated_r257_factor_rebuild_and_system_attachment_audit",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "question": (
            "Does rebuilding only the stale industry-relative-strength input in a temporary "
            "data directory restore R257 coverage through the production composite builder "
            "and production exact-date extension attachment, without changing registered data?"
        ),
        "hypothesis": (
            "Using current enriched K-lines to recalculate the production industry factor "
            "will restore broad R257 coverage for the frozen 2026-10-19 date through both "
            "the production composite builder and production attachment function."
        ),
        "frozen_scope": {
            "target_date": TARGET_DATE,
            "universe": "symbols present in the frozen alt_hs300 target-date partition",
            "market_input": "all frozen kline_daily_enriched partitions, used for rolling warm-up",
            "industry_function": "scripts/build_alt_factor_data.py::_industry_daily",
            "composite_function": (
                "scripts/build_r257_quality_relative_strength_factor.py::_factor_daily / _min_rank_factor"
            ),
            "system_attachment": (
                "backend/app/factors/ext_factors.py::attach_ext_columns(include_snapshot=False)"
            ),
            "parent_manifest": "evidence/r291_input_manifest.jsonl",
            "parent_manifest_sha256": parent_hash,
            "input_manifest": "evidence/r292_input_manifest.jsonl",
            "input_manifest_sha256": manifest_hash,
            "input_files": len(records),
            "kline_partition_count": len(market_paths),
            "required_paths_missing_from_parent_manifest": missing,
        },
        "decision_rule": {
            "pass": (
                "The scratch-only production builder produces at least 200 non-null R257 values "
                "on the target date, production attach_ext_columns attaches at least 200 to "
                "target-date enriched rows, and exact attached values equal the scratch factor "
                "partition for every non-null row."
            ),
            "fail": (
                "Any required frozen source hash changes, a production function fails, or either "
                "scratch composite or attached coverage is below 200."
            ),
            "interpretation": (
                "Pass proves isolated computational and system-attachment recoverability for the "
                "frozen historical date only; it does not reproduce old numeric values, establish "
                "point-in-time correctness, measure returns, or validate alpha."
            ),
        },
        "execution_limits": {
            "registered_market_or_ext_data_write": False,
            "provider_called": False,
            "builder_main_called": False,
            "backtest_or_matching_run": False,
            "returns_or_trades_read": False,
            "strategy_or_production_state_changed": False,
            "scratch_only_rebuild": True,
        },
        "panel_candidate": {
            "kind": "strategy",
            "name": "R292 隔离重建与系统装配验证",
            "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
            "data_as_of": TARGET_DATE,
            "status": "pending",
            "metrics": {},
            "config": PANEL_CONFIG,
        },
        "panel_guard": {
            "R288": "280b43c9d2364b5589d9d0edc73d1144",
            "R289": "58dc4329c85e4df88271aa9b42276089",
            "R290": "2451e5a386d5489584e4121f42d97792",
            "R291": "4b5134a2f8a1441d8034dbb484b8e238",
            "register_once": True,
            "required_status": "pending",
            "retry_post": False,
        },
    }
    prereg_payload = (json.dumps(preregistration, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)).encode("utf-8")
    prereg_hash = write_once(PREREGISTRATION, prereg_payload)
    print(json.dumps({
        "round": "R292",
        "input_files": len(records),
        "kline_partitions": len(market_paths),
        "input_manifest_sha256": manifest_hash,
        "preregistration_sha256": prereg_hash,
        "required_paths_missing_from_parent_manifest": missing,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
