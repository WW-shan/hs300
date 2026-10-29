#!/usr/bin/env python3
"""Freeze R296 one-date isolated factor-build and attachment audit inputs."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
TARGET_DATE = "2026-10-27"
API = "http://127.0.0.1:3018"
PREFIX = "r296_"

SOURCES = [
    "scripts/build_alt_factor_data.py",
    "scripts/build_r253_min_rank_factor.py",
    "scripts/build_r257_quality_relative_strength_factor.py",
    "backend/app/factors/ext_factors.py",
    "backend/app/factors/registry.py",
    "backend/app/services/ext_data.py",
    "backend/app/services/fs_utils.py",
    "backend/app/services/industry_map.py",
    "backend/app/backtest/fundamentals.py",
    "backend/app/api/hs300.py",
    "data/ext_data/alt_hs300/config.json",
    "data/ext_data/alt_hs300_minrank/config.json",
    "data/ext_data/alt_hs300_qrs/config.json",
    "data/ext_data/ext_hy_ths/config.json",
    "data/ext_data/ext_hy_ths/part.parquet",
    "data/research/r78_daily_membership/daily_snapshots.parquet",
    "data/research/r157_membership/monthly_snapshots.parquet",
    "data/financials/income/part.parquet",
    "data/financials/cash_flow/part.parquet",
]
RESEARCH_ROOTS = (
    "data/research/r87_sina_holder_full",
    "data/research/r163_holder_batch",
)

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
PANEL_CANDIDATE = {
    "kind": "strategy",
    "name": "R296 R257单日隔离重建与系统装配可行性",
    "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "config": PANEL_CONFIG,
    "metrics": {},
    "data_as_of": TARGET_DATE,
    "status": "pending",
}


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_bytes(payload)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def record(relative: str) -> dict:
    path = ROOT / relative
    stat = path.stat()
    return {
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "path": relative,
        "sha256": sha_file(path),
    }


def main() -> int:
    output_names = [
        "r296_official_members.json",
        "r296_official_members.json.sha256",
        "r296_input_manifest.jsonl",
        "r296_input_manifest.jsonl.sha256",
        "r296_preregistration.json",
        "r296_preregistration.json.sha256",
        Path(__file__).name + ".sha256",
    ]
    existing = [name for name in output_names if (EVIDENCE / name).exists()]
    if existing:
        raise RuntimeError(f"R296 freeze evidence exists; refusing to overwrite: {existing}")

    market_root = ROOT / "data/kline_daily_enriched"
    market_paths = sorted(market_root.glob("date=*/part.parquet"))
    if not market_paths or market_paths[-1].parent.name != f"date={TARGET_DATE}":
        raise RuntimeError("Target date is not the latest enriched market partition")

    relative_paths = set(SOURCES)
    relative_paths.update(path.relative_to(ROOT).as_posix() for path in market_paths)
    for root_name in RESEARCH_ROOTS:
        root = ROOT / root_name
        relative_paths.update(path.relative_to(ROOT).as_posix() for path in root.glob("*.parquet"))
    missing = sorted(path for path in relative_paths if not (ROOT / path).is_file())
    if missing:
        raise RuntimeError(f"Missing R296 frozen inputs: {missing}")

    request = urllib.request.Request(
        API + "/api/hs300/current", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"HS300 current-members endpoint returned HTTP {response.status}")
        official_raw = response.read()
    official = json.loads(official_raw)
    if official.get("count") != 300 or len(official.get("members", [])) != 300:
        raise RuntimeError("Official current-members response is incomplete")

    manifest_rows = [record(path) for path in sorted(relative_paths)]
    manifest = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in manifest_rows
    ).encode("utf-8")
    manifest_hash = sha_bytes(manifest)
    official_hash = sha_bytes(official_raw)
    official_path = EVIDENCE / "r296_official_members.json"
    write_once(official_path, official_raw)
    write_once(EVIDENCE / "r296_input_manifest.jsonl", manifest)

    prereg = {
        "round": "R296",
        "classification": "isolated_single_date_factor_build_and_system_attachment_feasibility_not_alpha_test",
        "status": "frozen_before_compute",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "research_question": (
            "Can production R253/R257 factor functions rebuild the latest enriched date in an isolated data root, "
            "and can the normal system extension attachment expose those values for current HS300 members?"
        ),
        "hypothesis": (
            "Recomputing the three financial inputs and industry-relative-strength input for the frozen target date, "
            "then running production R253/R257 ranking and attach_ext_columns, restores broad exact-date coverage "
            "without changing registered data."
        ),
        "prior_knowledge": (
            "R290-R294 established that R257 partitions stop at 2026-10-19, the latest enriched date is 2026-10-27, "
            "R292 restored coverage for 2026-10-19 in scratch, and the latest historical membership snapshot was "
            "previously observed at 2026-10-14. Those findings are known context, not blind evidence."
        ),
        "frozen_context": {
            "target_date": TARGET_DATE,
            "builder_universe": "production _calendar(): symbols from R87 holder files union R163 holder batches",
            "industry_window": "all frozen enriched partitions through target date for production rolling calculations",
            "financial_fields": [
                "management_expense_ratio_4q_low",
                "ocf_margin_4q",
                "gross_margin_yoy_improvement_q",
            ],
            "industry_field": "stock_rel_industry_20d",
            "membership_rule": "production R78 daily + R157 monthly snapshots, backward as-of target date; never select a future snapshot",
            "system_attachment": "app.factors.ext_factors.attach_ext_columns(include_snapshot=False), exact (symbol,date) join",
            "official_universe_endpoint": API + "/api/hs300/current",
            "official_snapshot": {
                "as_of": official.get("as_of"),
                "source": official.get("source"),
                "count": official.get("count"),
                "sha256": official_hash,
                "file": official_path.name,
            },
            "input_manifest": {
                "file": "r296_input_manifest.jsonl",
                "sha256": manifest_hash,
                "file_count": len(manifest_rows),
                "enriched_partition_count": len(market_paths),
                "source_files": sorted(SOURCES),
                "audit_scripts": [
                    Path(__file__).name,
                    "register_r296_panel_once.py",
                    "audit_r296_isolated_build_attachment.py",
                ],
            },
            "known_membership_lineage_risk": (
                "The historical snapshot may lag the official current list; report selected snapshot date, trading-session age, "
                "set overlap, and both current-list and snapshot-list coverage. Do not silently substitute universes."
            ),
        },
        "method": [
            "Hash and verify all frozen code/data inputs before and after the audit.",
            "Call production _calendar() read-only to obtain the research builder universe and full enriched date window.",
            "Call production _industry_daily() with full frozen date history; compute the three financial input functions only for the frozen target date.",
            "In a unique temporary root under data/.staging, use production R253 and R257 calculation/writer functions after redirecting their output roots to scratch; do not call any builder main().",
            "Call production attach_ext_columns() against a frozen target-date market frame with include_snapshot=False; compare attached values with staged output at exact symbol/date keys.",
            "Report coverage for the official current 300, the as-of historical membership set, and the full builder universe separately.",
        ],
        "precommitted_checks": {
            "I1_inputs_unchanged": "Every manifest file matches frozen size, mtime_ns and SHA-256 before and after execution.",
            "I2_exact_date": "Each generated factor table contains only the frozen target date and unique symbol keys.",
            "I3_quality_coverage": "At least 150 official current members have a non-null R253 value and at least 150 have a non-null R257 value.",
            "I4_system_attachment": "Every non-null staged R253/R257 value attaches at the same (symbol,date) key and matches exactly; no attachment-only values.",
            "I5_no_future_membership": "Selected historical membership snapshot is not later than target_date; its age and mismatch with official set are reported.",
            "I6_no_formal_writes": "No file under formal ext_data, enriched market, factor caches, strategy state or paper/production directories is changed.",
        },
        "interpretation_rules": [
            "I1-I6 can establish only isolated build and system attachment feasibility for this date, not alpha, investment return, PIT historical reconstruction, or production readiness.",
            "If attachment and coverage pass but membership freshness is stale, report data coverage restored while operational current-universe readiness remains failed.",
            "Any hash drift, missing dependency, duplicate key, unexpected date, or system attachment mismatch makes the audit incomplete; do not repair frozen inputs in place.",
            "No gate, strategy rule, fee, signal threshold, or production state may be changed based on R296.",
        ],
        "panel_candidate": PANEL_CANDIDATE,
        "scope_exclusions": [
            "No provider, network data pull, builder main(), backtest service, screener, strategy runner, matcher, order, or paper account is called.",
            "No returns, trade logs, or forward outcomes are read.",
            "No formal market, enriched, factor, configuration, cache, strategy, paper, or production file is written or changed.",
            "No alpha, independent sample, historical PIT validity, or deployable portfolio conclusion is tested.",
        ],
    }
    prereg_bytes = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    prereg_hash = write_once(EVIDENCE / "r296_preregistration.json", prereg_bytes)
    script_hash = sha_file(Path(__file__))
    with (EVIDENCE / (Path(__file__).name + ".sha256")).open("x", encoding="ascii") as stream:
        stream.write(f"{script_hash}  {Path(__file__).name}\n")
    result = {
        "round": "R296",
        "frozen_file_count": len(manifest_rows),
        "enriched_partitions": len(market_paths),
        "official_as_of": official.get("as_of"),
        "official_count": official.get("count"),
        "manifest_sha256": manifest_hash,
        "official_snapshot_sha256": official_hash,
        "preregistration_sha256": prereg_hash,
        "freeze_script_sha256": script_hash,
        "status": prereg["status"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
