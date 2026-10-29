#!/usr/bin/env python3
"""Finalize the acyclic hash chain for the R278 preregistration and panel audit."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r278_preregistration.json"
AUDIT = EVIDENCE / "r278_readiness_audit.json"
PANEL = EVIDENCE / "r278_panel_registration.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_sha(path: Path) -> str:
    digest = sha(path)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    actual_runner_sha = sha(EVIDENCE / "r278_readiness_audit.py")
    if prereg.get("runner_sha256") != actual_runner_sha:
        raise RuntimeError("R278 preregistration runner hash does not match executed source")
    prereg_sha = write_sha(PREREG)

    audit["preregistration_sha256"] = prereg_sha
    audit["source_hashes"]["preregistration_sha256"] = prereg_sha
    audit["source_hashes"]["runner_sha256"] = actual_runner_sha
    audit["source_hashes"].pop("panel_registration_proof_sha256", None)
    audit["prior_evidence_referenced"] = prereg["prior_evidence_referenced"]
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_sha = write_sha(AUDIT)

    panel["preregistration_sha256"] = prereg_sha
    panel["audit_result_sha256"] = audit_sha
    PANEL.write_text(json.dumps(panel, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    panel_sha = write_sha(PANEL)

    print(json.dumps({
        "preregistration_sha256": prereg_sha,
        "audit_sha256": audit_sha,
        "panel_registration_sha256": panel_sha,
        "all_readiness_gates_pass": audit["readiness_criteria"]["all_pass"],
        "panel_registration_pass": panel["panel_registration_pass"],
        "production_status": "pause_production",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
