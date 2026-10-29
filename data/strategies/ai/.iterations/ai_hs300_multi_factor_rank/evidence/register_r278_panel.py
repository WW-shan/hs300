#!/usr/bin/env python3
"""Validate the already captured, read-only R278 panel proof and save its hash."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PREREG = EVIDENCE / "r278_preregistration.json"
RESULT = EVIDENCE / "r278_readiness_audit.json"
PANEL = EVIDENCE / "r278_panel_registration.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    proof = json.loads(PANEL.read_text(encoding="utf-8"))
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    prereg_sha = sha(PREREG)
    if proof.get("preregistration_sha256") != prereg_sha:
        raise RuntimeError("Panel proof does not reference the current R278 preregistration")
    if proof.get("panel_registration_pass") is not True:
        raise RuntimeError("Frozen candidate is not correctly registered in the panel")
    if proof.get("candidate_research_only") is not True:
        raise RuntimeError("Candidate must remain research-only")
    if proof.get("candidate_source_sha256") != result.get("candidate_source", {}).get("sha256"):
        raise RuntimeError("Panel proof and readiness audit candidate source hashes differ")
    digest = sha(PANEL)
    checksum = PANEL.with_suffix(".json.sha256")
    if checksum.exists():
        if checksum.read_text(encoding="ascii").split()[0] != digest:
            raise RuntimeError("Existing R278 panel hash does not match its proof")
    else:
        checksum.write_text(f"{digest}  {PANEL.name}\n", encoding="ascii")
    print(json.dumps({"panel_proof": PANEL.relative_to(ROOT).as_posix(), "sha256": digest, "preregistration_sha256": prereg_sha, "panel_registration_pass": True, "candidate_research_only": True}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
