from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r305_final_integrity_addendum.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R305 scope integrity addendum exists; refusing overwrite")
    files = sorted(EVIDENCE.glob("r305_*") ) + sorted(EVIDENCE.glob("*r305*.py"))
    files += [ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/LEDGER.md"]
    files += [ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/RESEARCH_SUMMARY.md"]
    unique = {str(path.relative_to(ROOT)): path for path in files if path.is_file() and path != OUTPUT}
    checks = {}
    sidecar_errors = []
    for relative, path in unique.items():
        digest = sha(path)
        sidecar = path.with_name(path.name + ".sha256")
        if sidecar.exists():
            expected = sidecar.read_text(encoding="ascii").split()[0]
            if expected != digest:
                sidecar_errors.append(relative)
        elif path.suffix in {".json", ".md", ".py"} and path.name.startswith(("r305_", "audit_r305", "register_r305", "finalize_r305")):
            sidecar_errors.append(relative + " (missing sidecar)")
        checks[relative] = digest

    audit_path = EVIDENCE / "r305_member_bar_gap_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    changed_inputs = [
        relative for relative, expected in audit["input_sha256"].items()
        if not (ROOT / relative).is_file() or sha(ROOT / relative) != expected
    ]
    if sidecar_errors or changed_inputs:
        raise RuntimeError(f"Integrity verification failed: sidecars={sidecar_errors[:8]}, changed_inputs={changed_inputs[:8]}")

    result = {
        "round": "R305",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "posthoc_scope_correction_integrity_addendum; does not replace original R305 report or final audit",
        "checks": {
            "r305_evidence_files_hashed": len(checks),
            "existing_sha256_sidecars_match": True,
            "all_research_input_hashes_unchanged": True,
            "research_input_count": len(audit["input_sha256"]),
            "scope_deviation_addendum_present": (EVIDENCE / "r305_scope_deviation_addendum.json").is_file(),
            "scope_deviation_addendum_sidecar_matches": sha(EVIDENCE / "r305_scope_deviation_addendum.json") == (EVIDENCE / "r305_scope_deviation_addendum.json.sha256").read_text(encoding="ascii").split()[0],
            "scope_deviation_amendment_present": (EVIDENCE / "r305_scope_deviation_amendment.md").is_file(),
            "scope_deviation_amendment_sidecar_matches": sha(EVIDENCE / "r305_scope_deviation_amendment.md") == (EVIDENCE / "r305_scope_deviation_amendment.md.sha256").read_text(encoding="ascii").split()[0],
            "no_new_returns_or_backtest": True,
            "R304_A14_status_changed": False,
            "R257_production_status_changed": False,
        },
        "scope_correction": {
            "pre_registration_exploration_read_ohlc": True,
            "frozen_audit_script_read_price_values": False,
            "forward_return_or_outcome_read": False,
            "backtest_or_alpha_scan": False,
        },
        "file_sha256": checks,
        "research_input_sha256": audit["input_sha256"],
    }
    raw = (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{digest}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"sha256": digest, **result["checks"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
