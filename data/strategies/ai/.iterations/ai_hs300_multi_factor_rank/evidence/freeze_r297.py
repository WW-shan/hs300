#!/usr/bin/env python3
"""Freeze R297 counterfactual membership coverage inputs before computation."""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018"
TARGET = date(2026, 10, 27)
ROUND = "R297"
SCRIPT_NAMES = (
    "freeze_r297.py",
    "register_r297_panel_once.py",
    "audit_r297_counterfactual_membership.py",
)
FACTOR_TREES = (
    "data/ext_data/alt_hs300/timeseries",
    "data/ext_data/alt_hs300_minrank/timeseries",
    "data/ext_data/alt_hs300_qrs/timeseries",
)
PANEL_CONFIG = {
    "asset_type": "stock",
    "start": TARGET.isoformat(),
    "end": TARGET.isoformat(),
    "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "holding_days": 20,
    "max_positions": 10,
    "max_exposure_pct": 1.0,
    "initial_capital": 1_000_000.0,
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
CANDIDATE = {
    "kind": "strategy",
    "name": "R297 当前官方名单覆盖敏感性核验（反事实，非PIT）",
    "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "config": PANEL_CONFIG,
    "metrics": {},
    "data_as_of": TARGET.isoformat(),
    "status": "pending",
}
R296_EVIDENCE = (
    "r296_preregistration.json",
    "r296_input_manifest.jsonl",
    "r296_formal_factor_baseline.json",
    "r296_official_members.json",
    "r296_isolated_build_attachment_audit_v3.json",
    "r296_membership_eligibility_erratum.json",
    "r296_result_v3.md",
    "r296_round_index.json",
    "r296_round_index.md",
    "r296_round_index.json.sha256",
    "r296_round_index.md.sha256",
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def file_record(relative: str) -> dict:
    path = ROOT / relative
    stat = path.stat()
    return {
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "path": relative,
        "sha256": sha_file(path),
    }


def main() -> int:
    outputs = (
        "r297_official_current_members.json",
        "r297_official_current_members.json.sha256",
        "r297_input_manifest.jsonl",
        "r297_input_manifest.jsonl.sha256",
        "r297_formal_factor_baseline.json",
        "r297_formal_factor_baseline.json.sha256",
        "r297_preregistration.json",
        "r297_preregistration.json.sha256",
    )
    existing = [name for name in outputs if (EVIDENCE / name).exists()]
    if existing:
        raise RuntimeError(f"R297 freeze evidence exists; refusing overwrite: {existing}")

    r296_manifest_path = EVIDENCE / "r296_input_manifest.jsonl"
    r296_rows = [
        json.loads(line)
        for line in r296_manifest_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    relative_paths = {row["path"] for row in r296_rows}
    relative_paths.update(
        {
            "backend/app/hs300/current.py",
            "data/hs300/current_members.json",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r296_result_v3.md",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r296_isolated_build_attachment_audit_v3.json",
            "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r296_membership_eligibility_erratum.json",
        }
    )
    relative_paths.update(
        f"data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/{name}"
        for name in (*R296_EVIDENCE, *SCRIPT_NAMES)
    )
    missing = sorted(relative for relative in relative_paths if not (ROOT / relative).is_file())
    if missing:
        raise RuntimeError(f"R297 frozen inputs missing: {missing}")

    request = urllib.request.Request(
        API + "/api/hs300/current", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        official_raw = response.read()
        status = response.status
    official = json.loads(official_raw)
    if status != 200 or official.get("count") != 300 or len(official.get("members", [])) != 300:
        raise RuntimeError("System official-current endpoint did not return exactly 300 members")
    if official.get("source") not in {"csindex", "em"}:
        raise RuntimeError(f"Official-current source is not authoritative: {official.get('source')}")
    if len({item.get("symbol") for item in official["members"]}) != 300:
        raise RuntimeError("Official-current list contains duplicate symbols")
    official_hash = write_once(EVIDENCE / "r297_official_current_members.json", official_raw)

    rows = [file_record(relative) for relative in sorted(relative_paths)]
    manifest_bytes = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    manifest_hash = write_once(EVIDENCE / "r297_input_manifest.jsonl", manifest_bytes)

    trees = []
    for relative in FACTOR_TREES:
        partitions = sorted((ROOT / relative).glob("date=*/part.parquet"))
        trees.append(
            {
                "root": relative,
                "partition_count": len(partitions),
                "partitions": [
                    {
                        "mtime_ns": path.stat().st_mtime_ns,
                        "path": path.relative_to(ROOT).as_posix(),
                        "sha256": sha_file(path),
                        "size_bytes": path.stat().st_size,
                    }
                    for path in partitions
                ],
            }
        )
    baseline = {
        "round": ROUND,
        "classification": "pre_compute_formal_factor_tree_baseline",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "factor_trees": trees,
        "total_partition_count": sum(tree["partition_count"] for tree in trees),
    }
    baseline_bytes = (
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    baseline_hash = write_once(EVIDENCE / "r297_formal_factor_baseline.json", baseline_bytes)

    script_hashes = {name: sha_file(EVIDENCE / name) for name in SCRIPT_NAMES}
    for name, digest in script_hashes.items():
        sidecar = EVIDENCE / f"{name}.sha256"
        if sidecar.exists():
            if sidecar.read_text(encoding="ascii").split()[0] != digest:
                raise RuntimeError(f"Existing script sidecar does not match: {name}")
        else:
            sidecar.write_text(f"{digest}  {name}\n", encoding="ascii")

    prereg = {
        "round": ROUND,
        "classification": "counterfactual_membership_coverage_feasibility_not_PIT_or_alpha",
        "status": "frozen_before_compute",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "research_question": (
            "Is R296's only uncovered next-day official eligible member missing because required market/financial/industry inputs are absent, "
            "or because the historical membership snapshots exclude it; can production R253/R257 and system attachment produce the "
            "member when the official current list is supplied only as an isolated counterfactual snapshot?"
        ),
        "target_date": TARGET.isoformat(),
        "official_current_snapshot": {
            "as_of": official.get("as_of"),
            "source": official.get("source"),
            "fetched_at": official.get("fetched_at"),
            "count": official.get("count"),
            "sha256": official_hash,
            "file": "r297_official_current_members.json",
        },
        "counterfactual_definition": (
            "In a unique temporary data root only, append the frozen official-current 300-member list as a synthetic R78 snapshot "
            "effective on target_date. The list was observed the following calendar day and no official composition-change effective "
            "notice has been independently verified here; this scenario is solely a coverage sensitivity test, never historical membership, "
            "PIT evidence, a historical signal, or a production rule."
        ),
        "frozen_inputs": {
            "manifest": "r297_input_manifest.jsonl",
            "manifest_sha256": manifest_hash,
            "file_count": len(rows),
            "r296_manifest_sha256": sha_file(r296_manifest_path),
            "formal_factor_baseline": "r297_formal_factor_baseline.json",
            "formal_factor_baseline_sha256": baseline_hash,
            "formal_factor_partition_count": baseline["total_partition_count"],
            "script_sha256": script_hashes,
        },
        "precommitted_checks": {
            "I1_inputs_unchanged": "Every frozen source matches its pre-run size, mtime_ns and SHA-256 after execution.",
            "I2_new_member_inputs": "002294.SZ has a target-date enriched row, current industry mapping outside excluded industries, all three R253 financial inputs, and stock_rel_industry_20d.",
            "I3_counterfactual_eligible_coverage": "Both R253 and R257 are non-null for every industry-eligible member in the synthetic official-current target-date snapshot.",
            "I4_system_attachment": "Every non-null staged R253/R257 value attaches to the same symbol/date and equals it exactly; no attachment-only values.",
            "I5_formal_factor_trees_unchanged": "All formal factor partitions match their frozen SHA-256, size and mtime inventory.",
            "I6_no_outcome_read": "No returns/trades are read and no backtest, screener, strategy runner, matcher or paper operation is called.",
        },
        "interpretation_rules": [
            "A pass establishes only that membership input is the observed coverage boundary and production calculations/attachment can compute the member in isolation.",
            "The next-day official list is a counterfactual only; no historical/PIT claim is permitted.",
            "Do not change gates, factor thresholds, the strategy, historical membership or production state based on this audit.",
            "No alpha, return, portfolio, or deployability conclusion is tested.",
        ],
        "panel_candidate": {**CANDIDATE, "status": "pending"},
        "scope_exclusions": [
            "No formal market, enriched, historical membership, factor, cache, strategy, paper or production data is changed.",
            "No provider, data pull, builder main(), backtest service, screener, strategy runner, matcher, order or paper account is called.",
            "No return series, trade logs or forward outcomes are read.",
            "Panel registration is an intentional, separately evidenced research-panel write.",
        ],
    }
    prereg_bytes = (
        json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    prereg_hash = write_once(EVIDENCE / "r297_preregistration.json", prereg_bytes)
    print(
        json.dumps(
            {
                "round": ROUND,
                "status": prereg["status"],
                "target_date": prereg["target_date"],
                "official_as_of": official.get("as_of"),
                "official_source": official.get("source"),
                "frozen_file_count": len(rows),
                "formal_factor_partitions": baseline["total_partition_count"],
                "input_manifest_sha256": manifest_hash,
                "formal_factor_baseline_sha256": baseline_hash,
                "preregistration_sha256": prereg_hash,
                "script_sha256": script_hashes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
