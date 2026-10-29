from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r304_A12_final_record_addendum.json"
BASELINE_HASH = "e291b4d9f388279ecf06e0c88c57c8ec47761e1dc9727d136502dcc6ae13b94e"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    a12 = json.loads((EVIDENCE / "r304_A12_official_file_response.json").read_text(encoding="utf-8"))
    panel = json.loads((EVIDENCE / "r304_A11_panel_readback.json").read_text(encoding="utf-8"))
    audit = json.loads((EVIDENCE / "r304_A10_workbook_audit.json").read_text(encoding="utf-8"))
    if a12.get("http_status") != 200 or a12.get("response_sha256") != BASELINE_HASH or not a12.get("response_matches_A10_bytes"):
        raise RuntimeError("A12 one-time result does not match the retained A10 workbook")
    if a12.get("request_count") != 1 or a12.get("retry_count") != 0:
        raise RuntimeError("A12 request count mismatch")
    if not panel.get("all_pass") or panel.get("r304_candidate_status") != "pending":
        raise RuntimeError("A11 final panel readback failed")
    if not audit.get("count_reconciles_24_replacements") or audit.get("complete_hs300_snapshot_present"):
        raise RuntimeError("A10 workbook audit is inconsistent with PIT decision")

    names = [
        "r304_preregistration_amendment_A9.json", "run_r304_a9_single_detail_get.py",
        "r304_A9_panel_readback.raw", "r304_A9_panel_readback.json", "r304_A9_dispatch.json",
        "r304_A9_detail_response.raw", "r304_A9_detail_response.json",
        "r304_preregistration_amendment_A10.json", "run_r304_a10_single_attachment_get.py",
        "r304_A10_dispatch.json", "r304_A10_attachment_response.raw", "r304_A10_attachment_response.json",
        "audit_r304_a10_workbook.py", "r304_A10_workbook_audit.json", "r304_A10_analysis.json",
        "r304_preregistration_amendment_A11.json", "run_r304_a11_final_panel_get.py",
        "r304_A11_panel_readback.raw", "r304_A11_panel_readback.json",
        "r304_preregistration_amendment_A12.json", "run_r304_a12_single_snapshot_get.py",
        "r304_A12_dispatch.json", "r304_A12_official_file_response.raw", "r304_A12_official_file_response.json",
        "r304_A6_A7_erratum.json", "r304_A6_A7_erratum.md", "r304_final_report.md",
        "r304_final_evidence_record.json", "r304_A11_final_record_addendum.json",
    ]
    hashes = {name: sha(EVIDENCE / name) for name in names}
    hashes["r304_A12_final_record_addendum.py"] = sha(Path(__file__))
    record = {
        "round": "R304-A12",
        "classification": "append_only_final_evidence_reconciliation",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "a12": {
            "http_status": a12["http_status"], "request_count": a12["request_count"],
            "retry_count": a12["retry_count"], "bytes": a12["response_bytes"],
            "sha256": a12["response_sha256"], "same_as_A10": a12["response_matches_A10_bytes"],
            "interpretation": "same payload; no independent new historical evidence",
        },
        "panel_final": {
            "candidate_count": panel["candidate_count"], "r304_match_count": panel["r304_match_count"],
            "r304_status": panel["r304_candidate_status"],
            "all_existing_statuses_unchanged": panel["all_existing_statuses_unchanged_vs_A9"],
            "post_count": 0, "readback_sha256": sha(EVIDENCE / "r304_A11_panel_readback.json"),
        },
        "A10_delta": {
            "added_unique": audit["hs300_delta"]["调入"]["unique_code_count"],
            "removed_unique": audit["hs300_delta"]["调出"]["unique_code_count"],
            "complete_snapshot_present": False,
        },
        "pit_reconstruction": "no-go", "alpha_search_or_return_backtest": False,
        "production_mutation": False, "panel_mutation": False, "evidence_sha256": hashes,
    }
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("A12 final record already exists; refusing overwrite")
    raw = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    OUTPUT.write_bytes(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "a12_same_as_a10": record["a12"]["same_as_A10"],
        "panel_status": record["panel_final"]["r304_status"],
        "pit_reconstruction": record["pit_reconstruction"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
