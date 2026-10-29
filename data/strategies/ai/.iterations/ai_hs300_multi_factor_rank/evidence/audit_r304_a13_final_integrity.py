from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ITERATION = EVIDENCE.parent
OUTPUT = EVIDENCE / "r304_A13_final_integrity_addendum.json"
MARKER = "R304-A13_FINAL_INTEGRITY_CHECK"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("Final integrity addendum exists; refusing overwrite")

    final_path = EVIDENCE / "r304_A13_final_record_addendum.json"
    final_record = json.loads(final_path.read_text(encoding="utf-8"))
    checks = {}
    for name, expected in final_record["evidence_sha256"].items():
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256")
        current = sha(path) if path.exists() else None
        recorded_sidecar = sidecar.read_text(encoding="ascii").split()[0] if sidecar.exists() else None
        checks[name] = {
            "record_hash": expected,
            "current_hash": current,
            "sidecar_hash": recorded_sidecar,
            "matches": expected == current == recorded_sidecar,
        }
    if not all(item["matches"] for item in checks.values()):
        raise RuntimeError("A13 evidence manifest or sidecar mismatch")

    local_checks = {}
    for name in (
        "finalize_r304_a13_panel_addendum.py",
        "r304_A13_final_record_addendum.json",
        "r304_A13_final_report_addendum.md",
    ):
        path = EVIDENCE / name
        sidecar = path.with_name(path.name + ".sha256")
        current = sha(path)
        recorded = sidecar.read_text(encoding="ascii").split()[0] if sidecar.exists() else None
        local_checks[name] = {"current_hash": current, "sidecar_hash": recorded, "matches": current == recorded}
    if not all(item["matches"] for item in local_checks.values()):
        raise RuntimeError("Final record/report/script sidecar mismatch")

    registration = json.loads((EVIDENCE / "r304_A13_panel_registration.json").read_text(encoding="utf-8"))
    panel = {
        "counts": [registration["candidate_count_before"], registration["candidate_count_after"]],
        "a13_id": registration["candidate"]["id"],
        "a13_status": registration["candidate"]["status"],
        "r304_id": registration["existing_r304_candidate"]["id"],
        "r304_status": registration["existing_r304_candidate"]["status"],
        "prior_statuses_unchanged": registration["existing_statuses_unchanged_vs_A11"],
    }
    if panel["counts"] != [20, 21] or panel["a13_status"] != "pending" or panel["r304_status"] != "pending" or not panel["prior_statuses_unchanged"]:
        raise RuntimeError("Panel readback guard mismatch")

    ledger_checks = {}
    for name in ("LEDGER.md", "RESEARCH_SUMMARY.md"):
        content = (ITERATION / name).read_text(encoding="utf-8")
        ledger_checks[name] = content.count("R304-A13 面板登记收尾与最终核验") == 1
    if not all(ledger_checks.values()):
        raise RuntimeError("Final ledger entry missing or duplicated")

    result = {
        "round": "R304-A13",
        "classification": "append_only_final_hash_and_ledger_reconciliation",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "prior_final_record_sha256": sha(final_path),
        "prior_final_report_sha256": sha(EVIDENCE / "r304_A13_final_report_addendum.md"),
        "registered_evidence_count": len(checks),
        "registered_evidence_all_hashes_and_sidecars_match": True,
        "evidence_checks": checks,
        "final_record_report_and_script_sidecars": local_checks,
        "panel_readback": panel,
        "panel_get_raw_body_archived": False,
        "ledger_marker_exactly_once": ledger_checks,
        "pit_reconstruction": "no-go",
        "return_backtest_or_alpha_search": False,
        "production_mutation": False,
    }
    raw = (json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    with OUTPUT.with_name(OUTPUT.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n")

    entry = (
        "\r\nR304-A13_FINAL_INTEGRITY_CHECK: All 11 registered A13 evidence files and sidecars match; "
        "the finalizer and final record/report sidecars also match. Panel readback is 20->21; A13 and original R304 are pending; "
        "prior statuses are unchanged. The panel GET raw body was not archived; only its response hash and candidate summary remain. "
        "Each ledger contains one A13 closeout entry. This was local-only validation: no network request, backtest, alpha search, or production write. "
        "Details: evidence/r304_A13_final_integrity_addendum.json.\r\n"
    ).encode("ascii")
    for name in ("LEDGER.md", "RESEARCH_SUMMARY.md"):
        with (ITERATION / name).open("ab") as stream:
            stream.write(entry)

    print(json.dumps({
        "addendum_sha256": hashlib.sha256(raw).hexdigest(),
        "registered_evidence_count": len(checks),
        "all_hashes_match": True,
        "panel": panel,
        "ledger_marker_exactly_once": ledger_checks,
        "panel_get_raw_body_archived": False,
    }, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
