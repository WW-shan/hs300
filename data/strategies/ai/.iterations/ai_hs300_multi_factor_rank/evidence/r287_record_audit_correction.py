"""Append-only pre-run amendment documenting the R287 resolver-field correction."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r287_preregistration.json"
AUDIT = EVIDENCE / "r287_system_mask_audit.py"
AMENDMENT = EVIDENCE / "r287_preregistration_amendment.json"
AMENDMENT_2 = EVIDENCE / "r287_preregistration_amendment_v2.json"
AMENDMENT_3 = EVIDENCE / "r287_preregistration_amendment_v3.json"
AMENDMENT_4 = EVIDENCE / "r287_preregistration_amendment_v4.json"
AMENDMENT_5 = EVIDENCE / "r287_preregistration_amendment_v5.json"
AMENDMENT_6 = EVIDENCE / "r287_preregistration_amendment_v6.json"
AMENDMENT_7 = EVIDENCE / "r287_preregistration_amendment_v7.json"
AMENDMENT_8 = EVIDENCE / "r287_preregistration_amendment_v8.json"
AMENDMENT_9 = EVIDENCE / "r287_preregistration_amendment_v9.json"
AMENDMENT_10 = EVIDENCE / "r287_preregistration_amendment_v10.json"
AMENDMENT_11 = EVIDENCE / "r287_preregistration_amendment_v11.json"
AMENDMENT_12 = EVIDENCE / "r287_preregistration_amendment_v12.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_once(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")


def main() -> int:
    target = (
        AMENDMENT_12 if AMENDMENT_11.exists()
        else AMENDMENT_11 if AMENDMENT_10.exists()
        else AMENDMENT_10 if AMENDMENT_9.exists()
        else AMENDMENT_9 if AMENDMENT_8.exists()
        else AMENDMENT_8 if AMENDMENT_7.exists()
        else AMENDMENT_7 if AMENDMENT_6.exists()
        else AMENDMENT_6 if AMENDMENT_5.exists()
        else AMENDMENT_5 if AMENDMENT_4.exists()
        else AMENDMENT_4 if AMENDMENT_3.exists()
        else AMENDMENT_3 if AMENDMENT_2.exists()
        else AMENDMENT_2 if AMENDMENT.exists()
        else AMENDMENT
    )
    if target.exists() or target.with_name(target.name + ".sha256").exists():
        raise RuntimeError("R287 amendment already exists; refusing overwrite")
    pre_hash = sha(PRE)
    pre_sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if pre_hash != pre_sidecar:
        raise RuntimeError("R287 preregistration sidecar mismatch")
    current = sha(AUDIT)
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if current == prereg["audit_script_sha256"]:
        raise RuntimeError("R287 audit correction has not changed the runner hash")
    previous_path = {
        AMENDMENT_2: AMENDMENT,
        AMENDMENT_3: AMENDMENT_2,
        AMENDMENT_4: AMENDMENT_3,
        AMENDMENT_5: AMENDMENT_4,
        AMENDMENT_6: AMENDMENT_5,
        AMENDMENT_7: AMENDMENT_6,
        AMENDMENT_8: AMENDMENT_7,
        AMENDMENT_9: AMENDMENT_8,
        AMENDMENT_10: AMENDMENT_9,
        AMENDMENT_11: AMENDMENT_10,
        AMENDMENT_12: AMENDMENT_11,
    }.get(target)
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path else None
    previous_hash = sha(previous_path) if previous_path else None
    record = {
        "round": "R287",
        "classification": "pre_execution_runner_implementation_correction_no_metric_or_hypothesis_change",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": pre_hash,
        "supersedes_audit_script_sha256": previous["replacement_audit_script_sha256"] if previous else prereg["audit_script_sha256"],
        "replacement_audit_script_sha256": current,
        "failure": "Final preflight stopped because amendment v10 bound the runner hash before the amendment-chain manifest field was added.",
        "correction": "This v11 record binds the final current runner hash, which validates and serializes the append-only amendment chain before feature loading. No analytical definition changes.",
        "analysis_effect": "Preflight exited before feature-panel loading and did not read target-date feature values, gates, masks or scores. No result artifact exists.",
        "retry_policy": "Amendments v1-v10 remain immutable; no panel POST is repeated. This is the final runner hash binding for this execution.",
        "prior_amendment_sha256": previous_hash,
    }
    save_once(target, (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({
        "amendment_path": target.name,
        "amendment_sha256": sha(target),
        "superseded_audit_sha256": record["supersedes_audit_script_sha256"],
        "replacement_audit_sha256": current,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
