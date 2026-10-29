#!/usr/bin/env python3
"""Freeze R294's isolated directory-publication and cache-coherence probe."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
SOURCE_FILES = [
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/secondary-development.md",
    "scripts/build_alt_factor_data.py",
    "scripts/build_r253_min_rank_factor.py",
    "scripts/build_r257_quality_relative_strength_factor.py",
    "backend/app/services/fs_utils.py",
    "backend/app/services/ext_data.py",
    "backend/app/services/ext_pull.py",
    "backend/app/factors/ext_factors.py",
    "backend/app/services/industry_map.py",
    "backend/app/services/strategy_cache.py",
    "backend/app/tickflow/repository.py",
    "backend/app/api/ext_data.py",
    "backend/app/main.py",
    "backend/app/jobs/daily_pipeline.py",
    "backend/tests/test_atomic_parquet_writes.py",
    "backend/tests/test_ext_backfill.py",
    "data/ext_data/alt_hs300/config.json",
    "data/ext_data/alt_hs300_minrank/config.json",
    "data/ext_data/alt_hs300_qrs/config.json",
]
OUTPUTS = [
    EVIDENCE / "r294_preregistration.json",
    EVIDENCE / "r294_input_snapshot.json",
]
PANEL_CANDIDATE = {
    "kind": "strategy",
    "name": "R294 派生因子目录切换与缓存失效安全验证",
    "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "config": {
        "asset_type": "stock",
        "commission_pct": 0.0006,
        "end": "2026-10-19",
        "fees_pct": 0.0006,
        "holding_days": 20,
        "initial_capital": 1000000.0,
        "matching": "open_t+1",
        "max_exposure_pct": 1.0,
        "max_positions": 10,
        "minute_fill": False,
        "mode": "position",
        "overrides": {},
        "params": {},
        "position_sizing": "equal",
        "slippage_bps": 15.0,
        "stamp_tax_pct": 0.001,
        "start": "2026-10-19",
        "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
    },
    "metrics": {},
    "data_as_of": "2026-10-19",
    "status": "pending",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha(data)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def partition_inventory(config_id: str) -> dict:
    root = ROOT / "data" / "ext_data" / config_id / "timeseries"
    parts = sorted(root.glob("date=*/part.parquet"))
    dates = [path.parent.name[5:] for path in parts]
    return {
        "config_id": config_id,
        "partition_count": len(parts),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "partition_files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha(path.read_bytes()),
            }
            for path in parts
            if path.parent.name in {"date=2026-10-19", "date=2026-10-27"}
        ],
    }


def main() -> int:
    if any(path.exists() or Path(str(path) + ".sha256").exists() for path in OUTPUTS):
        raise RuntimeError("R294 freeze artifacts already exist; refusing to overwrite")

    sources = []
    for relative in SOURCE_FILES:
        data = (ROOT / relative).read_bytes()
        row = {"path": relative, "size_bytes": len(data), "sha256": sha(data)}
        if relative.endswith("/config.json"):
            config = json.loads(data)
            pull = config.get("pull")
            row.update({
                "config_id": config.get("id"),
                "pull_present": pull is not None,
                "pull_enabled": bool((pull or {}).get("enabled", False)),
            })
        sources.append(row)

    data_root = ROOT / "data"
    enriched = sorted((data_root / "kline_daily_enriched").glob("date=*/part.parquet"))
    enriched_dates = [path.parent.name[5:] for path in enriched]
    snapshot = {
        "round": "R294",
        "classification": "frozen_source_and_local_state_manifest",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "source_files": sources,
        "local_state": {
            "enriched_partition_count": len(enriched),
            "enriched_first_date": enriched_dates[0] if enriched_dates else None,
            "enriched_last_date": enriched_dates[-1] if enriched_dates else None,
            "factor_trees": [
                partition_inventory(config_id)
                for config_id in ("alt_hs300", "alt_hs300_minrank", "alt_hs300_qrs")
            ],
        },
        "scope": (
            "Test same-volume directory rename semantics and an injected interruption/rollback "
            "using synthetic files under data/.staging only; audit existing cache invalidation "
            "call paths and specify completeness gates for a future isolated full rebuild."
        ),
        "exclusions": [
            "Do not execute any production builder, provider, network pull, backtest, screening, or matching path.",
            "Do not read factor values or returns; only hash two existing partition files per factor tree.",
            "Do not write/delete/rename anything under data/ext_data or data/kline_daily_enriched.",
            "Do not change business code, cache files, strategy state, gate thresholds, or production status.",
            "Do not interpret this filesystem probe as proof of crash durability or alpha.",
        ],
        "panel_candidate": PANEL_CANDIDATE,
    }
    snapshot_hash = write_once(
        OUTPUTS[1], (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    prereg = {
        "round": "R294",
        "status": "frozen_before_probe",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "research_question": (
            "Can same-volume per-directory replacement support a safe staged publication with rollback, and what cache/view invalidation is required for readers to observe a complete derived-factor generation?"
        ),
        "hypotheses": [
            "A single directory rename can provide a per-tree namespace switch, but sequential publication of three trees is not a transaction.",
            "A journaled backup-and-replace sequence can recover an injected mid-switch failure while the process is alive; abrupt-crash recovery remains a separate requirement.",
            "Correct visibility requires clearing extension-frame/registry/strategy caches and the live repository enriched cache, and refreshing DuckDB views where used.",
        ],
        "method": [
            "Create synthetic old/new directory trees only below data/.staging and verify same-volume replacement behavior on Windows.",
            "Inject a failure between moving the old tree to backup and installing the staged tree; verify rollback restores the complete old tree and staged files remain recoverable.",
            "Trace actual extension factor, API, DuckDB, enriched repository and strategy cache invalidation functions.",
            "Define fail-closed full-build validation gates against the enriched trading-date set and upstream dependency dates.",
        ],
        "decision_rules": [
            "Call a switch atomic only for one directory namespace operation; do not label a multi-tree sequence atomic.",
            "Any missing/extra partition, date mismatch, duplicate symbol-date, schema mismatch, or component coverage failure must abort publication.",
            "A rollback test proves only the tested process-level failure path, not power-loss durability.",
            "A successful storage probe does not imply historic factor-value reproduction, PIT validity, returns, or alpha.",
        ],
        "input_snapshot": {"file": OUTPUTS[1].name, "sha256": snapshot_hash},
        "freeze_script_sha256": sha(Path(__file__).read_bytes()),
        "panel_candidate": PANEL_CANDIDATE,
    }
    prereg_hash = write_once(
        OUTPUTS[0], (json.dumps(prereg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    print(json.dumps({
        "round": "R294",
        "source_files": len(sources),
        "snapshot_sha256": snapshot_hash,
        "preregistration_sha256": prereg_hash,
        "enriched_partitions": len(enriched),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
