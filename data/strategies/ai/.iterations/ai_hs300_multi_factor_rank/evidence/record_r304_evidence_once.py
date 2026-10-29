from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A9.json"
PANEL_PROOF = EVIDENCE / "r304_A9_panel_readback.json"
RESPONSE = EVIDENCE / "r304_A9_detail_response.json"
WORKBOOK_AUDIT = EVIDENCE / "r304_A10_workbook_audit.json"
ANALYSIS = EVIDENCE / "r304_A10_analysis.json"
FINAL_PANEL = EVIDENCE / "r304_A11_panel_readback.json"
ERRATUM = EVIDENCE / "r304_A6_A7_erratum.json"
REPORT = EVIDENCE / "r304_final_report.md"
OUTPUT = EVIDENCE / "r304_A11_final_record_addendum.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n")


def main() -> None:
    prereg = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    panel = json.loads(PANEL_PROOF.read_text(encoding="utf-8"))
    response = json.loads(RESPONSE.read_text(encoding="utf-8"))
    audit = json.loads(WORKBOOK_AUDIT.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    erratum = json.loads(ERRATUM.read_text(encoding="utf-8"))
    required = (AMENDMENT, PANEL_PROOF, RESPONSE, WORKBOOK_AUDIT, ANALYSIS, FINAL_PANEL, ERRATUM, REPORT)
    if any(not path.is_file() for path in required):
        raise RuntimeError("R304 final record inputs incomplete")
    final_panel = json.loads(FINAL_PANEL.read_text(encoding="utf-8"))
    if prereg.get("amendment_id") != "R304-A9" or not panel.get("pending_unique"):
        raise RuntimeError("R304 amendment or panel proof failed final check")
    if final_panel.get("all_pass") is not True or final_panel.get("all_existing_statuses_unchanged_vs_A9") is not True:
        raise RuntimeError("R304 final read-only panel proof failed")
    if response.get("http_status") != 200 or response.get("request_count") != 1 or response.get("retry_count") != 0:
        raise RuntimeError("R304 A9 official response request count/status mismatch")
    if audit.get("count_reconciles_24_replacements") is not True or analysis.get("pit_feasibility") != "no-go":
        raise RuntimeError("R304 A10 audit or PIT decision failed final check")

    files = {path.name: sha(path) for path in required}
    for name in (
        "r304_A9_panel_readback.raw",
        "r304_A9_detail_response.raw",
        "r304_A10_dispatch.json",
        "r304_A10_attachment_response.raw",
        "r304_A10_attachment_response.json",
        "audit_r304_a10_workbook.py",
        "r304_A9_dispatch.json",
        "r304_A11_panel_readback.raw",
        "run_r304_a11_final_panel_get.py",
        "r304_preregistration_amendment_A11.json",
    ):
        path = EVIDENCE / name
        if not path.is_file():
            raise RuntimeError(f"R304 final evidence missing: {name}")
        files[name] = sha(path)

    record = {
        "round": "R304",
        "classification": "final_append_only_evidence_record_after_A11",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "prior_checkpoint_record": "r304_final_evidence_record.json",
        "prior_checkpoint_record_sha256": sha(EVIDENCE / "r304_final_evidence_record.json"),
        "a9_official_request_count": response["request_count"],
        "a10_attachment_request_count": 1,
        "panel_post_count_in_a9_a10": 0,
        "panel_candidate_id": prereg["panel"]["candidate_id"],
        "panel_candidate_status": panel["r304_candidate_status"],
        "panel_candidate_count": panel["candidate_count"],
        "panel_match_count": panel["r304_match_count"],
        "a10_added_count": audit["hs300_delta"]["调入"]["row_count"],
        "a10_removed_count": audit["hs300_delta"]["调出"]["row_count"],
        "a10_added_unique": audit["hs300_delta"]["调入"]["unique_code_count"],
        "a10_removed_unique": audit["hs300_delta"]["调出"]["unique_code_count"],
        "seed_for_2018_01_02_supported": False,
        "pit_reconstruction": "no-go",
        "alpha_search_or_return_backtest": False,
        "production_data_or_strategy_mutation": False,
        "a6_a7_erratum_sha256": sha(ERRATUM),
        "evidence_sha256": files,
    }
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R304 final evidence record exists; refusing overwrite")
    raw = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_once(OUTPUT, raw)
    print(json.dumps({
        "record_sha256": hashlib.sha256(raw).hexdigest(),
        "panel_candidate_status": record["panel_candidate_status"],
        "a10_added": record["a10_added_count"],
        "a10_removed": record["a10_removed_count"],
        "pit_reconstruction": record["pit_reconstruction"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
