#!/usr/bin/env python3
"""Run R297 after verifying its pre-compute scope and evidence chain."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[6]
SOURCE = EVIDENCE / "audit_r297_counterfactual_membership_v3.py"
PRE = EVIDENCE / "r297_preregistration.json"
MANIFEST = EVIDENCE / "r297_input_manifest.jsonl"
BASELINE = EVIDENCE / "r297_formal_factor_baseline.json"
SCOPE = EVIDENCE / "r297_scope_correction.json"
ADDENDUM = EVIDENCE / "r297_execution_erratum_v3.json"
CACHE = ROOT / "data/hs300/current_members.json"
OFFICIAL = EVIDENCE / "r297_official_current_members.json"
PANEL_PROOF = EVIDENCE / "r297_panel_registration.json"
PANEL_RECHECK = EVIDENCE / "r297_panel_status_recheck_v3.json"
RESULT = EVIDENCE / "r297_counterfactual_membership_audit_v3.json"
REPORT = EVIDENCE / "r297_result_v3.md"
PANEL_API = "http://127.0.0.1:3018/api/backtest/candidates"
CACHE_RELATIVE = "data/hs300/current_members.json"


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sidecar(path: Path) -> str:
    actual = sha_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    if actual != expected:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path.name}")
    return actual


def member_hash(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = sorted((row["symbol"], row.get("name", "")) for row in data["members"])
    return sha_bytes(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_bytes(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def panel_get() -> tuple[int, bytes]:
    request = urllib.request.Request(PANEL_API, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def verify_sources() -> dict:
    prereg_hash = verify_sidecar(PRE)
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R297" or prereg.get("status") != "frozen_before_compute":
        raise RuntimeError("R297 preregistration identity/status mismatch")

    manifest_hash = verify_sidecar(MANIFEST)
    if manifest_hash != prereg["frozen_inputs"]["manifest_sha256"]:
        raise RuntimeError("R297 frozen manifest identity mismatch")
    manifest_rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line]
    scope = json.loads(SCOPE.read_text(encoding="utf-8"))
    cache_row = next(row for row in manifest_rows if row["path"] == CACHE_RELATIVE)
    if cache_row["sha256"] != scope["observed_cache_before_compute"]["frozen_sha256"]:
        raise RuntimeError("Scope correction does not match the original cache manifest entry")
    semantic_hash = member_hash(OFFICIAL)
    if semantic_hash != scope["observed_cache_before_compute"]["frozen_semantic_members_sha256"]:
        raise RuntimeError("Frozen official membership semantic hash mismatch")
    if member_hash(CACHE) != semantic_hash:
        raise RuntimeError("Mutable current-members cache differs from the frozen official membership")

    checked_inputs = 0
    for row in manifest_rows:
        if row["path"] == CACHE_RELATIVE:
            continue
        path = ROOT / row["path"]
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns, sha_file(path)) != (row["bytes"], row["mtime_ns"], row["sha256"]):
            raise RuntimeError(f"Frozen input changed: {row['path']}")
        checked_inputs += 1

    baseline_hash = verify_sidecar(BASELINE)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    partitions = [row for tree in baseline["factor_trees"] for row in tree["partitions"]]
    for row in partitions:
        path = ROOT / row["path"]
        stat = path.stat()
        if (stat.st_size, stat.st_mtime_ns, sha_file(path)) != (row["size_bytes"], row["mtime_ns"], row["sha256"]):
            raise RuntimeError(f"Formal factor partition changed: {row['path']}")

    return {
        "preregistration_sha256": prereg_hash,
        "manifest_sha256": manifest_hash,
        "formal_factor_baseline_sha256": baseline_hash,
        "other_frozen_manifest_inputs_verified": checked_inputs,
        "formal_factor_partitions_verified": len(partitions),
        "runtime_cache_raw_hash_frozen": False,
        "runtime_cache_semantic_members_equal_official_snapshot": True,
        "runtime_cache_semantic_sha256": semantic_hash,
    }


def verify_panel_once() -> dict:
    previous = json.loads(PANEL_PROOF.read_text(encoding="utf-8"))
    if previous.get("classification") != "panel_registration_and_readback_proof":
        raise RuntimeError("R297 original panel registration proof has an unexpected classification")
    candidate_id = previous["candidate"]["id"]
    status, raw = panel_get()
    if status != 200:
        raise RuntimeError(f"R297 panel GET status recheck failed: HTTP {status}")
    data = json.loads(raw)
    items = data if isinstance(data, list) else data.get("items", [])
    matches = [item for item in items if item.get("id") == candidate_id]
    if len(matches) != 1 or matches[0].get("status") != "pending":
        raise RuntimeError("R297 panel candidate is missing, duplicated or no longer pending")
    if len(items) != previous["candidate_count_after"]:
        raise RuntimeError("R297 panel candidate count changed since original registration proof")
    record = {
        "round": "R297",
        "classification": "panel_status_read_only_recheck_before_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": status,
        "candidate_count": len(items),
        "candidate_id": candidate_id,
        "candidate_status": matches[0]["status"],
        "response_sha256": sha_bytes(raw),
        "post_sent": False,
    }
    proof_hash = write_once(PANEL_RECHECK, (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    record["proof_sha256"] = proof_hash
    return record


def main() -> int:
    outputs = (PANEL_RECHECK, RESULT, RESULT.with_name(RESULT.name + ".sha256"), REPORT, REPORT.with_name(REPORT.name + ".sha256"))
    if any(path.exists() for path in outputs):
        raise RuntimeError("R297 v3 output exists; refusing overwrite or duplicate panel evidence")
    source_hashes = {
        SOURCE.name: verify_sidecar(SOURCE),
        Path(__file__).name: verify_sidecar(Path(__file__)),
        SCOPE.name: verify_sidecar(SCOPE),
        ADDENDUM.name: verify_sidecar(ADDENDUM),
    }
    addendum = json.loads(ADDENDUM.read_text(encoding="utf-8"))
    if addendum.get("v3_audit_script_sha256") != source_hashes[SOURCE.name] or addendum.get("v3_runner_script_sha256") != source_hashes[Path(__file__).name]:
        raise RuntimeError("R297 v3 execution erratum does not bind the current scripts")

    before = verify_sources()
    panel_record = verify_panel_once()
    after_panel = verify_sources()
    if before != after_panel:
        raise RuntimeError("Frozen inputs changed during read-only panel status recheck")

    spec = importlib.util.spec_from_file_location("r297_counterfactual_audit_v2", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load R297 v3 isolated audit")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    original_verify = module.verify_manifest

    def scoped_verify(prereg: dict) -> list[dict]:
        raw = MANIFEST.read_bytes()
        if sha_bytes(raw) != prereg["frozen_inputs"]["manifest_sha256"]:
            raise RuntimeError("R297 input manifest changed")
        rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
        verified_rows = []
        for row in rows:
            if row["path"] == CACHE_RELATIVE:
                if member_hash(CACHE) != member_hash(OFFICIAL):
                    raise RuntimeError("Runtime cache differs semantically from frozen official membership")
                continue
            path = ROOT / row["path"]
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns, sha_file(path)) != (row["bytes"], row["mtime_ns"], row["sha256"]):
                raise RuntimeError(f"Frozen input changed: {row['path']}")
            verified_rows.append(row)
        return verified_rows

    module.verify_manifest = scoped_verify
    module.SCOPE_AUDIT_METADATA = {
        "classification": "precompute_scope_correction_and_execution_erratum",
        "scope_correction_sha256": source_hashes[SCOPE.name],
        "execution_erratum_sha256": source_hashes[ADDENDUM.name],
        "v2_audit_script_sha256": source_hashes[SOURCE.name],
        "v2_runner_script_sha256": source_hashes[Path(__file__).name],
        "panel_status_recheck_sha256": panel_record["proof_sha256"],
        "runtime_cache_raw_hash_frozen": False,
        "runtime_cache_semantic_members_equal": True,
        "other_frozen_manifest_inputs": before["other_frozen_manifest_inputs_verified"],
        "precheck": before,
    }
    try:
        module.main()
    finally:
        module.verify_manifest = original_verify

    after = verify_sources()
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    result["scope_correction"].update(
        {
            "execution_erratum_v3_sha256": source_hashes[ADDENDUM.name],
            "v3_audit_script_sha256": source_hashes[SOURCE.name],
            "v3_runner_script_sha256": source_hashes[Path(__file__).name],
        }
    )
    if result.get("scope_correction", {}).get("panel_status_recheck_sha256") != panel_record["proof_sha256"]:
        raise RuntimeError("R297 machine result is not bound to its panel status readback proof")
    if result.get("execution_limits", {}).get("formal_factor_trees_unchanged") is not True:
        raise RuntimeError("R297 isolated audit did not confirm formal factor tree immutability")
    print(
        json.dumps(
            {
                "round": "R297",
                "status": result["status"],
                "panel_status_recheck": panel_record,
                "verified_before": before,
                "verified_after": after,
                "machine_result_sha256": sha_file(RESULT),
                "report_sha256": sha_file(REPORT),
                "machine_sidecar_matches": verify_sidecar(RESULT) == sha_file(RESULT),
                "report_sidecar_matches": verify_sidecar(REPORT) == sha_file(REPORT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
