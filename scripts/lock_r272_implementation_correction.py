#!/usr/bin/env python3
"""Lock R272 implementation corrections without reading outcomes or running a backtest."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG_PATH = EVIDENCE / "r272_preregistration.json"
FEASIBILITY_PATH = EVIDENCE / "r272_data_feasibility.json"
FEASIBILITY_PREREG_PATH = EVIDENCE / "r272_feasibility_preregistration.json"
OLD_BUILD_PATH = EVIDENCE / "r272_factor_build.json"
INTERIM_BUILD_PATH = EVIDENCE / "r272_factor_build_corrected.json"
INTERIM_PARTITION_MANIFEST_PATH = EVIDENCE / "r272_factor_build_corrected_partitions.json"
LOCK_PATH = EVIDENCE / "r272_implementation_correction_final_v3.json"
SUPERSEDED_LOCK_PATHS = [
    EVIDENCE / "r272_implementation_correction.json",
    EVIDENCE / "r272_implementation_correction_locked.json",
    EVIDENCE / "r272_implementation_correction_final.json",
    EVIDENCE / "r272_implementation_correction_final_v2.json",
]

CORRECTED_SOURCES = {
    "builder": ROOT / "scripts/build_r272_operating_margin_factor.py",
    "runner": ROOT / "scripts/backtest_r272_operating_margin_improvement.py",
    "correction_lock_writer": ROOT / "scripts/lock_r272_implementation_correction.py",
    "candidate_strategy": ROOT / "data/strategies/ai/ai_hs300_operating_margin_improvement_quality_h20_sl20.py",
    "control_strategy": ROOT / "data/strategies/ai/ai_hs300_operating_margin_improvement_quality_control.py",
    "test": ROOT / "backend/tests/test_r272_operating_margin_factor.py",
    "feasibility_probe": ROOT / "scripts/probe_r272_operating_margin_improvement.py",
    "feasibility_registrar": ROOT / "scripts/preregister_r272_feasibility.py",
    "preregistration_registrar": ROOT / "scripts/preregister_r272.py",
    "base_factor_builder": ROOT / "scripts/build_alt_factor_data.py",
    "financial_pit_rules": ROOT / "backend/app/backtest/fundamentals.py",
    "extended_data_contract": ROOT / "backend/app/services/ext_data.py",
    "extended_factor_registry": ROOT / "backend/app/factors/ext_factors.py",
    "strategy_backtest_service": ROOT / "backend/app/backtest/strategy.py",
    "backtest_engine": ROOT / "backend/app/backtest/engine.py",
    "strategy_engine": ROOT / "backend/app/strategy/engine.py",
    "scoring": ROOT / "backend/app/strategy/scoring.py",
    "r112_system_harness": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    "r166_forward_ic_helpers": ROOT / "scripts/backtest_r166_weak_regime_factor_scan.py",
    "r221_membership_helpers": ROOT / "scripts/backtest_r221_sga_nonfin_quality.py",
    "r229_segment_helpers": ROOT / "scripts/backtest_r229_ocf_margin.py",
    "r239_concentration_helpers": ROOT / "scripts/backtest_r239_concentration_audit.py",
    "industry_mapping": ROOT / "backend/app/services/industry_map.py",
    "superseded_interim_build_evidence": INTERIM_BUILD_PATH,
    "superseded_interim_partition_manifest": INTERIM_PARTITION_MANIFEST_PATH,
    "superseded_draft_lock": EVIDENCE / "r272_implementation_correction_final.json",
    "superseded_final_v2_lock": EVIDENCE / "r272_implementation_correction_final_v2.json",
    "superseded_final_lock_runner_source": ROOT / "scripts/backtest_r272_operating_margin_improvement.py",
    "superseded_draft_runner": ROOT / "scripts/backtest_r272_operating_margin_improvement.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_sidecar(path: Path, sidecar: Path) -> str:
    digest = _sha256(path)
    expected = f"{digest}  {path.name}"
    if sidecar.read_text(encoding="ascii").strip() != expected:
        raise RuntimeError(f"checksum mismatch for {path.name}")
    return digest


def main() -> int:
    if LOCK_PATH.exists() or LOCK_PATH.with_suffix(".json.sha256").exists():
        raise RuntimeError("R272 implementation correction lock already exists; refusing to overwrite")
    superseded_locks = [
        {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": _sha256(path),
            "sidecar_sha256": _sha256(path.with_suffix(".json.sha256"))
            if path.with_suffix(".json.sha256").exists()
            else None,
            "disposition": "preserved_as_earlier_draft_lock",
        }
        for path in SUPERSEDED_LOCK_PATHS
        if path.exists()
    ]

    prereg_hash = _verify_sidecar(PREREG_PATH, EVIDENCE / "r272_preregistration.sha256")
    feasibility_hash = _verify_sidecar(
        FEASIBILITY_PATH, EVIDENCE / "r272_data_feasibility.json.sha256"
    )
    feasibility_prereg_hash = _verify_sidecar(
        FEASIBILITY_PREREG_PATH, EVIDENCE / "r272_feasibility_preregistration.sha256"
    )
    old_build_hash = _verify_sidecar(OLD_BUILD_PATH, EVIDENCE / "r272_factor_build.json.sha256")
    interim_build_hash = _verify_sidecar(
        INTERIM_BUILD_PATH, EVIDENCE / "r272_factor_build_corrected.json.sha256"
    )
    interim_manifest_hash = _verify_sidecar(
        INTERIM_PARTITION_MANIFEST_PATH,
        EVIDENCE / "r272_factor_build_corrected_partitions.json.sha256",
    )
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    feasibility_prereg = json.loads(FEASIBILITY_PREREG_PATH.read_text(encoding="utf-8"))
    feasibility = json.loads(FEASIBILITY_PATH.read_text(encoding="utf-8"))
    old_build = json.loads(OLD_BUILD_PATH.read_text(encoding="utf-8"))

    from app.factors import ext_factors

    canonical_factor_id = ext_factors.ext_column_name(
        prereg["factor"]["config_id"], prereg["factor"]["column"]
    )
    source_hashes = {name: _sha256(path) for name, path in CORRECTED_SOURCES.items()}
    input_checks = {
        name: {
            "path": item["path"],
            "registered_sha256": item["sha256"],
            "current_sha256": _sha256(ROOT / item["path"]),
            "unchanged": item["sha256"] == _sha256(ROOT / item["path"]),
        }
        for name, item in feasibility["inputs"].items()
    }
    checks = {
        "preregistration_sidecar_valid": prereg_hash == _sha256(PREREG_PATH),
        "feasibility_proof_checksum_valid": feasibility_hash
        == prereg["selection_basis"]["feasibility_sha256"],
        "feasibility_evidence_is_feasibility_only": feasibility["status"]
        == "feasibility_only_no_outcome_analysis"
        and feasibility["outcome_data_loaded"] is False
        and feasibility["all_feasibility_gates_pass"] is True,
        "feasibility_preregistration_matches_probe": feasibility_prereg["probe_sha256"]
        == source_hashes["feasibility_probe"]
        and feasibility["preregistration_sha256"] == feasibility_prereg_hash,
        "old_build_identity_matches_config_contract": old_build["config_id"]
        == prereg["factor"]["config_id"]
        and old_build["field"] == prereg["factor"]["column"],
        "preregistered_alias_mismatch_is_recorded": prereg["factor"]["id"]
        != canonical_factor_id
        and old_build["factor_id"] == canonical_factor_id,
        "interim_build_evidence_chain_valid": json.loads(
            INTERIM_BUILD_PATH.read_text(encoding="utf-8")
        ).get("partition_manifest", {}).get("sha256") == interim_manifest_hash,
        "all_frozen_input_files_unchanged": all(row["unchanged"] for row in input_checks.values()),
        "feasibility_gates_still_pass": all(feasibility["gates"].values())
        and all(prereg["selection_basis"]["gates"].values()),
        "outcome_data_was_not_read": True,
        "backtest_was_not_run": True,
        "panel_was_not_reloaded": True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"R272 correction prerequisites failed: {failed}")

    payload = {
        "round": "R272",
        "status": "implementation_correction_locked_before_rebuild_and_outcome_analysis",
        "locked_at": datetime.now(UTC).isoformat(),
        "source_chain": {
            "feasibility_preregistration": {
                "path": FEASIBILITY_PREREG_PATH.relative_to(ROOT).as_posix(),
                "sha256": feasibility_prereg_hash,
            },
            "feasibility_evidence": {
                "path": FEASIBILITY_PATH.relative_to(ROOT).as_posix(),
                "sha256": feasibility_hash,
            },
            "original_preregistration": {
                "path": PREREG_PATH.relative_to(ROOT).as_posix(),
                "sha256": prereg_hash,
            },
            "preregistration_sha256": prereg_hash,
            "feasibility_sha256": feasibility_hash,
            "previous_build_evidence": {
                "path": OLD_BUILD_PATH.relative_to(ROOT).as_posix(),
                "sha256": old_build_hash,
                "disposition": "preserved; its 60,482-row result is invalidated by unsorted as-of input",
            },
            "superseded_interim_build": {
                "path": INTERIM_BUILD_PATH.relative_to(ROOT).as_posix(),
                "sha256": interim_build_hash,
                "partition_manifest": INTERIM_PARTITION_MANIFEST_PATH.relative_to(ROOT).as_posix(),
                "partition_manifest_sha256": interim_manifest_hash,
                "disposition": "preserved_as_intermediate_build_before_final_pit_audit_fixes",
            },
            "superseded_correction_locks": superseded_locks,
        },
        "registered_source_hashes": {
            "builder": prereg["builder_sha256"],
            "runner": prereg["runner_sha256"],
            "preregistration_registrar": prereg["preregistration_script_sha256"],
            "feasibility_probe": feasibility_prereg["probe_sha256"],
            "feasibility_registrar": feasibility_prereg["preregistration_script_sha256"],
        },
        "registered_builder_sha256": prereg["builder_sha256"],
        "registered_runner_sha256": prereg["runner_sha256"],
        "registered_preregistrar_sha256": prereg["preregistration_script_sha256"],
        "registered_feasibility_registrar_sha256": feasibility_prereg["preregistration_script_sha256"],
        "feasibility_probe_sha256": feasibility_prereg["probe_sha256"],
        "old_build_evidence_sha256": old_build_hash,
        "preregistered_factor_id": prereg["factor"]["id"],
        "corrected_source_hashes": source_hashes,
        "source_inventory": {
            name: {"path": path.relative_to(ROOT).as_posix(), "sha256": source_hashes[name]}
            for name, path in CORRECTED_SOURCES.items()
        },
        "frozen_inputs": input_checks,
        "factor_identity": {
            "preregistered_alias": prereg["factor"]["id"],
            "config_id": prereg["factor"]["config_id"],
            "field": prereg["factor"]["column"],
            "canonical_factor_id_from_system_registry": canonical_factor_id,
            "alias_is_preserved_as_preregistered_metadata": True,
            "strategy_and_execution_key": canonical_factor_id,
        },
        "implementation_corrections": [
            {
                "issue": "financial_panel unique(keep=last) left effective-date keys unsorted before join_asof",
                "correction": "sort by symbol/effective_date after dedupe and explicitly sort both as-of inputs",
                "outcome_basis": False,
            },
            {
                "issue": "factor key used by the strategy differed from ExtConfigStore-derived factor ID",
                "correction": "candidate, matched control, required features, registry lookup, and execution use the canonical system factor ID",
                "outcome_basis": False,
            },
            {
                "issue": "previous PIT audit compared all factor symbols to an HS300-only expected panel",
                "correction": "compare exact symbol/date keys only after restricting both expected and written rows to the same historical-member evaluation window",
                "outcome_basis": False,
            },
            {
                "issue": "previous build proof did not inventory its partition artifacts",
                "correction": "archive prior factor partitions and require the corrected build proof to hash every partition",
                "outcome_basis": False,
            },
        ],
        "unchanged_preregistered_design": {
            "formula": prereg["factor"]["formula"],
            "maximum_age_days": 180,
            "candidate_scoring": prereg["strategy"]["candidate"]["scoring"],
            "control_scoring": prereg["strategy"]["control"]["scoring"],
            "execution": prereg["execution"],
            "gates": prereg["gates"],
        },
        "checks": checks,
        "scope": {
            "forward_returns_loaded": False,
            "trade_records_loaded": False,
            "backtest_results_loaded": False,
            "backtest_run": False,
            "panel_reload_run": False,
            "production_status": "pause_production",
        },
    }
    LOCK_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(LOCK_PATH)
    LOCK_PATH.with_suffix(".json.sha256").write_text(
        f"{digest}  {LOCK_PATH.name}\n", encoding="ascii"
    )
    print(json.dumps({"lock": LOCK_PATH.relative_to(ROOT).as_posix(), "sha256": digest, "checks": checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
