#!/usr/bin/env python3
"""Register the frozen R289 diagnostic once and preserve panel evidence."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r289_preregistration.json"
INTENT = EVIDENCE / "r289_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r289_panel_registration_post.json"
READBACK_PROOF = EVIDENCE / "r289_panel_registration.json"
BASE = "http://127.0.0.1:3018"


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
    req = urllib.request.Request(BASE + "/api/backtest/candidates", data=body, method=method, headers=headers)
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
        raise RuntimeError("R289 panel evidence already exists; refusing duplicate POST")
    prereg_hash = sha_file(PRE)
    if PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0] != prereg_hash:
        raise RuntimeError("R289 preregistration checksum mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R289" or prereg.get("status") != "frozen_before_outcome_read":
        raise RuntimeError("R289 preregistration identity or status mismatch")

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
        raise RuntimeError(f"Matching R289 panel candidate already exists: {duplicates[0].get('id')}")
    r288 = [row for row in items if row.get("id") == prereg["panel_guard"]["r288_candidate_id"]]
    if len(r288) != 1 or r288[0].get("status") != "pending":
        raise RuntimeError("R288 panel candidate is missing or no longer pending")

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
        "round": "R289",
        "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_hash,
        "preflight_http_status": status,
        "preflight_response_body_sha256": sha(raw),
        "preflight_candidate_count": len(items),
        "matching_candidates_before_post": len(duplicates),
        "r288_candidate_id": r288[0]["id"],
        "r288_status_before_post": r288[0]["status"],
        "request_body": body_payload,
        "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    # A POST is dispatched once; any transport ambiguity is recorded without retry.
    post_status, post_raw, post_error = request("POST", body)
    try:
        post_body = json.loads(post_raw) if post_raw else None
    except json.JSONDecodeError:
        post_body = None
    post_record = {
        "round": "R289",
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
    r288_after = [row for row in items_after if row.get("id") == prereg["panel_guard"]["r288_candidate_id"]]
    proof = {
        "round": "R289",
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
        "candidate_count": len(items_after),
        "candidate": created[0] if len(created) == 1 else None,
        "matching_candidate_count": len(created),
        "r288_candidate": r288_after[0] if len(r288_after) == 1 else None,
        "registration_pass": bool(
            post_status == 200 and read_status == 200 and created_id
            and len(created) == 1 and created[0].get("status") == "pending"
            and created[0].get("source_id") == candidate["source_id"]
            and len(r288_after) == 1 and r288_after[0].get("status") == "pending"
        ),
    }
    proof_hash = save_once(READBACK_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({
        "registration_pass": proof["registration_pass"],
        "candidate_id": created_id,
        "post_http_status": post_status,
        "readback_http_status": read_status,
        "candidate_status": created[0].get("status") if len(created) == 1 else None,
        "r288_status": r288_after[0].get("status") if len(r288_after) == 1 else None,
        "proof_sha256": proof_hash,
        "transport_error": post_error or read_error,
    }, ensure_ascii=False, indent=2))
    return 0 if proof["registration_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
