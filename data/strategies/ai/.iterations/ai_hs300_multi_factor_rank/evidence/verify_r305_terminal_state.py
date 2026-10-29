from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r305_terminal_integrity_record.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.is_file():
        raise RuntimeError(f"Missing SHA-256 sidecar: {path}")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha(path)
    if actual != expected:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path}")
    return actual


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("Terminal R305 integrity record exists; refusing overwrite")

    research_root = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank"
    ledger = research_root / "LEDGER.md"
    summary = research_root / "RESEARCH_SUMMARY.md"
    ledger_addendum = EVIDENCE / "r305_ledger_addendum.md"
    summary_addendum = EVIDENCE / "r305_summary_addendum.md"
    ledger_text = ledger.read_text(encoding="utf-8").rstrip()
    ledger_addendum_text = ledger_addendum.read_text(encoding="utf-8").strip()
    if not ledger_text.endswith(ledger_addendum_text):
        raise RuntimeError("Current ledger does not end with the separately hashed R305 ledger addendum")
    summary_bytes = summary.read_bytes()
    summary_text = summary.read_text(encoding="utf-8").rstrip()
    summary_addendum_text = summary_addendum.read_text(encoding="utf-8").strip()
    if not summary_text.endswith(summary_addendum_text):
        raise RuntimeError("Current summary does not end with the separately hashed R305 summary addendum")

    evidence_files = sorted(
        path for path in EVIDENCE.iterdir()
        if path.is_file()
        and path.name.startswith(("r305_", "audit_r305_", "register_r305_", "capture_r305_", "finalize_r305_", "verify_r305_"))
        and not path.name.endswith(".sha256")
        and path != OUTPUT
    )
    evidence_hashes = {str(path.relative_to(ROOT)): verify_sidecar(path) for path in evidence_files}
    script_path = Path(__file__).resolve()
    script_hash = verify_sidecar(script_path)
    workspace_hashes = {
        str(path.relative_to(ROOT)): sha(path)
        for path in (ledger, summary)
    }

    audit_path = EVIDENCE / "r305_member_bar_gap_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    changed_inputs = [
        relative for relative, expected in audit["input_sha256"].items()
        if not (ROOT / relative).is_file() or sha(ROOT / relative) != expected
    ]
    if changed_inputs:
        raise RuntimeError(f"Research inputs changed since R305 audit: {changed_inputs[:10]}")

    panel = json.loads((EVIDENCE / "r305_final_panel_status.json").read_text(encoding="utf-8"))
    raw_panel = json.loads((EVIDENCE / "r305_final_panel_status.raw.json").read_text(encoding="utf-8"))
    r305_id = "cc701e29d6b1450982d2375dd50ce08b"
    matches = [item for item in raw_panel.get("items", []) if str(item.get("id")) == r305_id]
    first_final = json.loads((EVIDENCE / "r305_final_integrity_audit.json").read_text(encoding="utf-8"))
    statuses = {str(item.get("id")): item.get("status") for item in raw_panel.get("items", [])}
    old_statuses_unchanged = all(
        statuses.get(key) == value for key, value in first_final["protected_statuses"].items()
    )
    if panel.get("http_status") != 200 or len(raw_panel.get("items", [])) != 23 or len(matches) != 1 or matches[0].get("status") != "pending" or not old_statuses_unchanged:
        raise RuntimeError("Final panel state differs from the registered R305 state")

    result = {
        "round": "R305",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "terminal_read_only_integrity_closeout_after_documented_scope_and_hash_errata",
        "checks": {
            "current_R305_evidence_files_sidecars_match": len(evidence_hashes),
            "current_research_input_files_unchanged": True,
            "research_input_count": len(audit["input_sha256"]),
            "panel_http_200": panel["http_status"] == 200,
            "panel_candidate_count": len(raw_panel["items"]),
            "R305_id": r305_id,
            "R305_unique_and_pending": True,
            "all_existing_candidate_statuses_unchanged": old_statuses_unchanged,
            "ledger_contains_hashed_R305_addendum": True,
            "summary_contains_hashed_R305_addendum": True,
            "no_return_backtest_alpha_scan_or_production_write_in_closeout": True,
        },
        "current_workspace_sha256": workspace_hashes,
        "terminal_verifier_sha256": script_hash,
        "current_evidence_sha256": evidence_hashes,
        "research_input_sha256": audit["input_sha256"],
        "scope_and_record_correction_note": "Earlier R305 integrity artifacts remain immutable snapshots from their capture times. The scope deviation and a final-status hash-key erratum disclose the pre-registration OHLC exploration and a basename-versus-relative-path lookup error; this terminal record independently checks all current sidecars, current workspace text, source input hashes, and the current panel state.",
    }
    raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{digest}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"terminal_record_sha256": digest, **result["checks"], "ledger_sha256": workspace_hashes[str(ledger.relative_to(ROOT))], "summary_sha256": workspace_hashes[str(summary.relative_to(ROOT))]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
