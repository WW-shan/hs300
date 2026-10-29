#!/usr/bin/env python3
"""Freeze R291's read-only stale-factor root-cause audit inputs."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent


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
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    prereg_path = EVIDENCE / "r291_preregistration.json"
    manifest_path = EVIDENCE / "r291_input_manifest.jsonl"
    script_sidecar = EVIDENCE / "freeze_r291_preregistration.py.sha256"
    if any(path.exists() for path in (prereg_path, manifest_path, script_sidecar)):
        raise RuntimeError("R291 freeze evidence exists; refusing to overwrite")

    paths = set((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
    paths.update((DATA / "research" / "r87_sina_holder_full").glob("*.parquet"))
    paths.update((DATA / "research" / "r163_holder_batch").glob("*.parquet"))
    paths.update(
        ROOT / relative
        for relative in (
            "scripts/build_alt_factor_data.py",
            "scripts/build_r253_min_rank_factor.py",
            "scripts/build_r257_quality_relative_strength_factor.py",
            "backend/app/services/industry_map.py",
            "data/ext_data/ext_hy_ths/part.parquet",
            "data/ext_data/alt_hs300/config.json",
            "data/ext_data/alt_hs300_minrank/config.json",
            "data/ext_data/alt_hs300_qrs/config.json",
            "data/ext_data/alt_hs300/timeseries/date=2026-10-14/part.parquet",
            "data/ext_data/alt_hs300/timeseries/date=2026-10-16/part.parquet",
            "data/ext_data/alt_hs300/timeseries/date=2026-10-19/part.parquet",
            "data/ext_data/alt_hs300_minrank/timeseries/date=2026-10-16/part.parquet",
            "data/ext_data/alt_hs300_minrank/timeseries/date=2026-10-19/part.parquet",
            "data/ext_data/alt_hs300_qrs/timeseries/date=2026-10-16/part.parquet",
            "data/ext_data/alt_hs300_qrs/timeseries/date=2026-10-19/part.parquet",
            "data/research/r157_membership/monthly_snapshots.parquet",
            "data/research/r78_daily_membership/daily_snapshots.parquet",
            "data/strategies/ai/ai_hs300_quality_relative_strength_h20_sl20.py",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_factor_build.json",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r290_system_attachment_audit_v2.json",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r290_panel_registration.json",
        )
    )
    missing = sorted(path for path in paths if not path.is_file())
    if missing:
        raise RuntimeError(f"Missing frozen inputs: {[str(path) for path in missing]}")

    manifest_rows = []
    for path in sorted(paths):
        stat = path.stat()
        manifest_rows.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest(path),
        })
    manifest = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in manifest_rows
    )
    manifest_hash = write_once(manifest_path, manifest)

    candidate = {
        "kind": "strategy",
        "name": "R291 R257基础因子陈旧分区根因审计",
        "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "config": {
            "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
            "start": "2026-10-19",
            "end": "2026-10-19",
            "params": {}, "overrides": {}, "matching": "open_t+1",
            "fees_pct": 0.0006, "commission_pct": 0.0006, "stamp_tax_pct": 0.001,
            "slippage_bps": 15.0, "max_positions": 10, "max_exposure_pct": 1.0,
            "initial_capital": 1000000.0, "position_sizing": "equal", "mode": "position",
            "holding_days": 20, "asset_type": "stock", "minute_fill": False,
        },
        "metrics": {}, "data_as_of": "2026-10-19", "status": "pending",
    }
    prereg = {
        "round": "R291",
        "classification": "read_only_stale_derived_factor_root_cause_audit",
        "registered_at_utc": datetime.now(UTC).isoformat(),
        "status": "frozen_before_results",
        "question": (
            "Did the 2026-10-19 alt_hs300 coverage collapse arise because derived factor partitions "
            "were built before enriched K-line partitions were refreshed, and does the current "
            "production factor calculation restore the missing coverage?"
        ),
        "hypothesis": (
            "The base factor partition is stale relative to refreshed enriched K-line inputs. "
            "The industry mapping, current builder calculation, and R257 formula remain usable; "
            "the saved custom-factor table was not regenerated after source data changed."
        ),
        "decision_rule": {
            "support_stale_derived_output": (
                "The current builder calculation yields at least 500/557 non-null industry-relative "
                "strength values on 2026-10-19; the saved partition has at most 50/557; the saved "
                "derived partition predates its current enriched K-line partition by at least one day; "
                "and rebuilding the R257 composite in memory with current relative-strength values "
                "restores at least 200 values from the existing quality component."
            ),
            "qualification": (
                "Without a frozen pre-refresh K-line copy, timestamps support stale lineage but "
                "cannot prove the exact prior input values or identify which upstream refresh changed them."
            ),
        },
        "frozen_scope": {
            "dates": ["2026-10-14", "2026-10-16", "2026-10-19", "2026-10-27"],
            "universe": "the 557 symbols selected by build_alt_factor_data._calendar()",
            "recomputed_function": "scripts/build_alt_factor_data.py::_industry_daily",
            "composite_function": "scripts/build_r257_quality_relative_strength_factor.py::_min_rank_factor",
            "input_manifest": manifest_path.name,
            "input_manifest_sha256": manifest_hash,
            "input_files": len(manifest_rows),
        },
        "panel_candidate": candidate,
        "panel_guard": {
            "R288_candidate_id": "280b43c9d2364b5589d9d0edc73d1144",
            "R289_candidate_id": "58dc4329c85e4df88271aa9b42276089",
            "R290_candidate_id": "2451e5a386d5489584e4121f42d97792",
            "required_status": "pending", "register_once": True, "retry_post": False,
        },
        "limits": {
            "backtest_run": False, "provider_called": False, "builder_write_called": False,
            "factor_or_market_data_written": False, "returns_or_trades_read": False,
            "strategy_or_production_state_changed": False,
        },
    }
    prereg_bytes = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    prereg_hash = write_once(prereg_path, prereg_bytes)
    script_hash = digest(Path(__file__))
    with script_sidecar.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{script_hash}  {Path(__file__).name}\n")
    print(json.dumps({
        "round": "R291", "input_files": len(manifest_rows),
        "input_manifest_sha256": manifest_hash, "preregistration_sha256": prereg_hash,
        "freeze_script_sha256": script_hash, "registered_name": candidate["name"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
