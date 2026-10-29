#!/usr/bin/env python3
"""Correct R280's namespace mapping from factor IDs to extension parquet columns."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
BASE = EVIDENCE / "r280_preregistration.json"
CORRECTION = EVIDENCE / "r280_audit_correction_preregistration.json"
SOURCE = EVIDENCE / "r280_snapshot_feasibility_audit.json"
OUTPUT = EVIDENCE / "r280_snapshot_feasibility_audit_corrected.json"


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> int:
    if OUTPUT.exists() or Path(str(OUTPUT) + ".sha256").exists():
        raise RuntimeError("Corrected R280 evidence exists; refusing overwrite")
    base_hash = sha_file(BASE)
    base_sidecar = Path(str(BASE) + ".sha256").read_text(encoding="ascii").split()[0]
    if base_hash != base_sidecar:
        raise RuntimeError("Base R280 preregistration checksum mismatch")
    correction_hash = sha_file(CORRECTION)
    correction_sidecar = Path(str(CORRECTION) + ".sha256").read_text(encoding="ascii").split()[0]
    if correction_hash != correction_sidecar:
        raise RuntimeError("R280 correction preregistration checksum mismatch")
    correction = json.loads(CORRECTION.read_text(encoding="utf-8-sig"))
    runner_hash = sha_file(Path(__file__).resolve())
    if runner_hash != correction["runner_sha256"]:
        raise RuntimeError("Correction runner differs from its preregistration")
    source_hash = sha_file(SOURCE)
    source_sidecar = Path(str(SOURCE) + ".sha256").read_text(encoding="ascii").split()[0]
    if source_hash != source_sidecar or source_hash != correction["original_audit_sha256"]:
        raise RuntimeError("Original audit checksum differs from correction preregistration")
    audit = json.loads(SOURCE.read_text(encoding="utf-8"))
    if audit.get("preregistration_sha256") != base_hash:
        raise RuntimeError("Original audit does not reference the frozen R280 preregistration")

    config_fields = {
        name.removeprefix("ext_alt_hs300_")
        for name in audit["partition_schemas"]["alt_hs300"]["columns"]
    }
    alt_columns = set(audit["partition_schemas"]["alt_hs300"]["columns"])
    enriched_columns = set(audit["partition_schemas"]["enriched_daily"]["columns"])
    corrected = {}
    for factor_id, previous in audit["required_ext_features"].items():
        physical_name = factor_id.removeprefix("ext_alt_hs300_")
        corrected[factor_id] = {
            "physical_partition_column": physical_name,
            "registered_in_alt_config": physical_name in config_fields,
            "column_in_latest_alt_partition": physical_name in alt_columns,
            "column_in_standard_enriched_partition": factor_id in enriched_columns,
            "previous_mapping": previous,
        }
    audit["required_ext_features"] = corrected
    audit["checks"]["all_candidate_ext_features_available_in_alt_source_partition"] = all(
        item["registered_in_alt_config"] and item["column_in_latest_alt_partition"]
        for item in corrected.values()
    )
    # The snapshot context is built from enriched data only; source availability does not imply context injection.
    audit["checks"]["all_candidate_ext_features_present_in_standard_enriched_context"] = all(
        item["column_in_standard_enriched_partition"] for item in corrected.values()
    )
    audit["checks"]["financial_pit_columns_present_in_standard_enriched_context"] = all(
        audit["financial_columns_available_in_enriched"].values()
    )
    audit["checks"]["local_daily_and_alt_dates_align"] = (
        audit["dates"]["enriched_daily_latest_partition"]
        == audit["dates"]["alt_hs300_latest_partition"]
    )
    audit["checks"]["local_inputs_align_to_current_members_date"] = (
        audit["dates"]["enriched_daily_latest_partition"]
        == audit["dates"]["current_hs300_members_as_of"]
        == audit["dates"]["alt_hs300_latest_partition"]
    )
    audit["checks"]["panel_registration_proof_pass"] = bool(
        audit["checks"]["panel_registration_proof_pass"]
    )
    audit["all_pass"] = all(audit["checks"].values())
    audit["decision"] = "not_ready_no_signal_generated" if not audit["all_pass"] else audit["decision"]
    audit["evidence_correction"] = {
        "classification": "append_only_deterministic_evidence_correction",
        "correction_preregistration_sha256": correction_hash,
        "correction_runner_sha256": runner_hash,
        "original_audit_path": SOURCE.relative_to(ROOT).as_posix(),
        "original_audit_sha256": source_hash,
        "reason": "R280 v1 compared the ext_alt_hs300_* factor ID directly to unprefixed physical alt_hs300 parquet/config field names. This correction uses the registered physical field name while leaving the frozen readiness gates unchanged.",
    }
    corrected_hash = write_json(OUTPUT, audit)
    print(json.dumps({
        "corrected_evidence": OUTPUT.relative_to(ROOT).as_posix(),
        "sha256": corrected_hash,
        "checks": audit["checks"],
        "all_pass": audit["all_pass"],
        "required_ext_features": audit["required_ext_features"],
    }, ensure_ascii=False, indent=2))
    return 0 if audit["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
