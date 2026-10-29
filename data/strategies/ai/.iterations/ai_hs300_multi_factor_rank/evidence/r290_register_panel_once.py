#!/usr/bin/env python3
"""Register the frozen R290 system attachment diagnostic once."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r290_preregistration.json"
INTENT = EVIDENCE / "r290_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r290_panel_registration_post.json"
READBACK_PROOF = EVIDENCE / "r290_panel_registration.json"
BASE = "http://127.0.0.1:3018"
R288_ID = "280b43c9d2364b5589d9d0edc73d1144"
R289_ID = "58dc4329c85e4df88271aa9b42276089"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def save_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request(method: str, body: bytes | None = None):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE + "/api/backtest/candidates",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", f"{type(error).__name__}: {error}"


def main() -> int:
    evidence_paths = (INTENT, POST_PROOF, READBACK_PROOF)
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in evidence_paths):
        raise RuntimeError("R290 panel evidence exists; refusing duplicate POST")
    prereg_hash = sha_file(PRE)
    sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sidecar != prereg_hash:
        raise RuntimeError("R290 preregistration checksum mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R290" or prereg.get("status") != "frozen_before_system_attachment_result":
        raise RuntimeError("R290 preregistration identity or status mismatch")

    candidate = prereg["panel_candidate"]
    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"Panel preflight GET failed: HTTP={status}, error={error}")
    listing = json.loads(raw)
    items = listing.get("items", [])
    duplicates = [
        row for row in items
        if row.get("source_id") == candidate["source_id"]
        and row.get("name") == candidate["name"]
        and row.get("config", {}).get("start") == candidate["config"]["start"]
        and row.get("config", {}).get("end") == candidate["config"]["end"]
    ]
    if duplicates:
        raise RuntimeError(f"Matching R290 panel candidate already exists: {duplicates[0].get('id')}")
    for guard_id, label in ((R288_ID, "R288"), (R289_ID, "R289")):
        matches = [row for row in items if row.get("id") == guard_id]
        if len(matches) != 1 or matches[0].get("status") != "pending":
            raise RuntimeError(f"{label} panel candidate is missing or no longer pending")

    body_payload = {
        "kind": "strategy",
        "name": candidate["name"],
        "source_id": candidate["source_id"],
        "config": candidate["config"],
        "metrics": {},
        "data_as_of": candidate["config"]["end"],
        "status": "pending",
    }
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R290",
        "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_hash,
        "preflight_http_status": status,
        "preflight_response_body_sha256": sha(raw),
        "preflight_candidate_count": len(items),
        "matching_candidates_before_post": len(duplicates),
        "guard_status_before_post": {
            "R288": next(row["status"] for row in items if row.get("id") == R288_ID),
            "R289": next(row["status"] for row in items if row.get("id") == R289_ID),
        },
        "request_body": body_payload,
        "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    # Dispatch the state-changing POST exactly once; record ambiguous transport without retry.
    post_status, post_raw, post_error = request("POST", body)
    try:
        post_body = json.loads(post_raw) if post_raw else None
    except json.JSONDecodeError:
        post_body = None
    post_record = {
        "round": "R290",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "response_body_sha256": sha(post_raw) if post_raw else None,
        "response_body": post_body,
    }
    post_hash = save_once(POST_PROOF, (json.dumps(post_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    read_status, read_raw, read_error = request("GET")
    try:
        read_listing = json.loads(read_raw) if read_raw else {}
    except json.JSONDecodeError:
        read_listing = {}
    created_id = post_body.get("id") if isinstance(post_body, dict) else None
    items_after = read_listing.get("items", [])
    created = [row for row in items_after if row.get("id") == created_id] if created_id else []
    guards_after = {
        label: [row for row in items_after if row.get("id") == guard_id]
        for guard_id, label in ((R288_ID, "R288"), (R289_ID, "R289"))
    }
    proof = {
        "round": "R290",
        "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "api_base": BASE,
        "preregistration_sha256": prereg_hash,
        "registration_intent_sha256": intent_hash,
        "post_proof_sha256": post_hash,
        "post_http_status": post_status,
        "post_transport_error": post_error,
        "readback_http_status": read_status,
        "readback_transport_error": read_error,
        "readback_response_body_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(items),
        "candidate_count_after": len(items_after),
        "candidate": created[0] if len(created) == 1 else None,
        "matching_candidate_count": len(created),
        "guard_candidates": {
            label: rows[0] if len(rows) == 1 else None for label, rows in guards_after.items()
        },
        "registration_pass": bool(
            post_status == 200 and read_status == 200 and created_id
            and len(created) == 1 and created[0].get("status") == "pending"
            and created[0].get("source_id") == candidate["source_id"]
            and created[0].get("data_as_of") == candidate["config"]["end"]
            and all(len(rows) == 1 and rows[0].get("status") == "pending" for rows in guards_after.values())
        ),
    }
    proof_hash = save_once(READBACK_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({
        "registration_pass": proof["registration_pass"],
        "candidate_id": created_id,
        "post_http_status": post_status,
        "readback_http_status": read_status,
        "candidate_status": created[0].get("status") if len(created) == 1 else None,
        "candidate_count_before": len(items),
        "candidate_count_after": len(items_after),
        "proof_sha256": proof_hash,
        "transport_error": post_error or read_error,
    }, ensure_ascii=False, indent=2))
    return 0 if proof["registration_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
