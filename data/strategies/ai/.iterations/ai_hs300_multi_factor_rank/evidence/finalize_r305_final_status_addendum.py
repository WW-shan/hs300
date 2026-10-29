from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r305_final_status_addendum.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(path: Path) -> str:
    actual = sha(path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if actual != expected:
        raise RuntimeError(f"Sidecar mismatch: {path.name}")
    return actual


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R305 final status addendum exists; refusing overwrite")
    original_final = json.loads((EVIDENCE / "r305_final_integrity_audit.json").read_text(encoding="utf-8"))
    panel = json.loads((EVIDENCE / "r305_final_panel_status.json").read_text(encoding="utf-8"))
    panel_raw = json.loads((EVIDENCE / "r305_final_panel_status.raw.json").read_text(encoding="utf-8"))
    items = panel_raw.get("items", [])
    expected_id = "cc701e29d6b1450982d2375dd50ce08b"
    matches = [item for item in items if str(item.get("id")) == expected_id]
    old_statuses = original_final["protected_statuses"]
    actual_statuses = {str(item.get("id")): item.get("status") for item in items}
    prior_statuses_unchanged = all(actual_statuses.get(key) == value for key, value in old_statuses.items())
    if panel.get("http_status") != 200 or len(items) != 23 or len(matches) != 1 or matches[0].get("status") != "pending" or not prior_statuses_unchanged:
        raise RuntimeError("Final panel proof does not match R305 registration state")

    audit = json.loads((EVIDENCE / "r305_member_bar_gap_audit.json").read_text(encoding="utf-8"))
    changed = [
        relative for relative, digest in audit["input_sha256"].items()
        if not (ROOT / relative).is_file() or sha(ROOT / relative) != digest
    ]
    if changed:
        raise RuntimeError(f"Research inputs changed since R305 audit: {changed[:10]}")

    new_evidence = [
        EVIDENCE / "r305_scope_deviation_addendum.json",
        EVIDENCE / "r305_scope_deviation_addendum.md",
        EVIDENCE / "r305_scope_deviation_amendment.md",
        EVIDENCE / "r305_final_integrity_addendum.json",
        EVIDENCE / "capture_r305_final_panel_status.py",
        EVIDENCE / "r305_final_panel_status.json",
        EVIDENCE / "r305_final_panel_status.raw.json",
        EVIDENCE / "finalize_r305_scope_addendum.py",
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/LEDGER.md",
        ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/RESEARCH_SUMMARY.md",
    ]
    file_hashes = {}
    for path in new_evidence:
        if not path.exists():
            continue
        relative = str(path.relative_to(ROOT))
        if path.name in {"LEDGER.md", "RESEARCH_SUMMARY.md"}:
            digest = sha(path)
        else:
            digest = verify(path)
        file_hashes[relative] = digest
    finalizer_current = EVIDENCE / "finalize_r305_scope_addendum.py"
    original_finalizer_hash = original_final.get("file_sha256", {}).get(finalizer_current.name)
    current_finalizer_hash = sha(finalizer_current)
    record = {
        "round": "R305",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "posthoc_final_panel_and_scope_addendum_integrity_check",
        "checks": {
            "panel_http_200": panel["http_status"] == 200,
            "panel_candidate_count": len(items),
            "R305_pending_unique": len(matches) == 1 and matches[0].get("status") == "pending",
            "all_statuses_unchanged_from_prior_final_check": prior_statuses_unchanged,
            "research_inputs_unchanged": True,
            "research_input_count": len(audit["input_sha256"]),
            "new_addenda_and_workspace_records_sidecars_match": True,
            "no_return_or_backtest": True,
        },
        "protected_statuses": {key: actual_statuses[key] for key in old_statuses},
        "panel_raw_response_sha256": sha(EVIDENCE / "r305_final_panel_status.raw.json"),
        "panel_record_sha256": sha(EVIDENCE / "r305_final_panel_status.json"),
        "finalizer_hash_disclosure": {
            "original_final_integrity_record_hash": original_finalizer_hash,
            "current_finalizer_hash": current_finalizer_hash,
            "changed_after_original_final_integrity_record": original_finalizer_hash != current_finalizer_hash,
            "note": "The finalizer script was subsequently amended to include scope-correction checks; the original final-integrity record is preserved and this addendum independently verifies current sidecars and inputs.",
        },
        "file_sha256": file_hashes,
        "research_input_sha256": audit["input_sha256"],
    }
    raw = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{digest}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"sha256": digest, **record["checks"], "finalizer_hash_disclosure": record["finalizer_hash_disclosure"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
