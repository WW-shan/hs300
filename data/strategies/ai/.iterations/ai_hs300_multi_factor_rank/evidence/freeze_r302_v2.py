from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
TRADE_CACHE = ROOT / "data/research/r299_cost_audit_cache"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    script = EVIDENCE / "r302_aggregate_frozen_trades_v2.py"
    manifest = EVIDENCE / "r302_v2_input_manifest.jsonl"
    amendment = EVIDENCE / "r302_v2_analysis_amendment.json"
    for path in (manifest, manifest.with_name(manifest.name + ".sha256"), amendment,
                 amendment.with_name(amendment.name + ".sha256"),
                 script.with_name(script.name + ".sha256")):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path.name}")

    script_hash = sha(script)
    script.with_name(script.name + ".sha256").write_text(
        f"{script_hash}  {script.name}\n", encoding="ascii"
    )

    prior_manifest = EVIDENCE / "r302_analysis_input_manifest.jsonl"
    records = {row["path"]: row for row in (
        json.loads(line) for line in prior_manifest.read_text(encoding="utf-8").splitlines() if line
    )}
    paths = [
        TRADE_CACHE / "ai_hs300_quality_relative_strength_h20_sl20_3x_2023-05-04_2024-12-31.trades.json",
        TRADE_CACHE / "ai_hs300_quality_relative_strength_h20_sl20_3x_2025-01-02_2026-10-16.trades.json",
        TRADE_CACHE / "ai_hs300_quality_relative_strength_control_3x_2023-05-04_2024-12-31.trades.json",
        TRADE_CACHE / "ai_hs300_quality_relative_strength_control_3x_2025-01-02_2026-10-16.trades.json",
        script,
        EVIDENCE / "r299_result.json",
    ]
    for path in paths:
        stat = path.stat()
        records[path.relative_to(ROOT).as_posix()] = {
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": sha(path),
        }

    manifest_bytes = b"".join(
        (json.dumps(row, sort_keys=True) + "\n").encode("utf-8")
        for row in records.values()
    )
    manifest.write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    manifest.with_name(manifest.name + ".sha256").write_text(
        f"{manifest_hash}  {manifest.name}\n", encoding="ascii"
    )

    payload = {
        "round": "R302",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "classification": "aggregation_definition_correction_frozen_before_revised_execution",
        "parent_registration_candidate_id": "f438e8b03baa464d9bf7b44d891580c9",
        "parent_registration_status": "pending",
        "registration_success_proof_sha256": sha(EVIDENCE / "r302_panel_readback.json"),
        "preregistration_sha256": sha(EVIDENCE / "r302_preregistration.json"),
        "input_manifest_sha256": manifest_hash,
        "input_count": len(records),
        "aggregation_script_sha256": script_hash,
        "correction": "v2 sums positive trade P&L within each symbol to measure that symbol's gross profit, then divides the leading net-winning symbols' gross profit by total trade gross profit. Per-symbol net-P&L shares and all trade totals are unchanged. The v1 result and script remain immutable.",
        "no_new_backtest": True,
        "thresholds_changed": False,
        "outcome_access_before_this_amendment": "v1 descriptive output was computed and reviewed. The correction is post hoc, does not change candidate selection, thresholds, or net-P&L concentration, and is disclosed as such.",
    }
    amendment_bytes = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    amendment.write_bytes(amendment_bytes)
    amendment.with_name(amendment.name + ".sha256").write_text(
        f"{hashlib.sha256(amendment_bytes).hexdigest()}  {amendment.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "manifest_count": len(records), "manifest_sha256": manifest_hash,
        "amendment_sha256": hashlib.sha256(amendment_bytes).hexdigest(),
        "aggregation_script_sha256": script_hash,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
