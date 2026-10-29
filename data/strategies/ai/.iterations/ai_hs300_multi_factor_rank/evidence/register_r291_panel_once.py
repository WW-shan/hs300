#!/usr/bin/env python3
"""Register the frozen R291 diagnostic on the local research panel once."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r291_preregistration.json"
BASE = "http://127.0.0.1:3018"
GUARDS = {
    "R288": "280b43c9d2364b5589d9d0edc73d1144",
    "R289": "58dc4329c85e4df88271aa9b42276089",
    "R290": "2451e5a386d5489584e4121f42d97792",
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = sha(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


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
    proof_paths = [
        EVIDENCE / "r291_panel_registration_intent.json",
        EVIDENCE / "r291_panel_registration_post.json",
        EVIDENCE / "r291_panel_registration.json",
    ]
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in proof_paths):
        raise RuntimeError("R291 panel proof exists; refusing duplicate registration")
    prereg_hash = sha(PRE.read_bytes())
    sidecar_hash = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar_hash:
        raise RuntimeError("R291 preregistration checksum mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R291" or prereg.get("status") != "frozen_before_results":
        raise RuntimeError("R291 preregistration identity/status mismatch")

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"Panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    candidate = prereg["panel_candidate"]
    duplicates = [
        item for item in items
        if item.get("name") == candidate["name"]
        and item.get("source_id") == candidate["source_id"]
        and item.get("config", {}).get("start") == candidate["config"]["start"]
        and item.get("config", {}).get("end") == candidate["config"]["end"]
    ]
    if duplicates:
        raise RuntimeError(f"Matching R291 candidate already exists: {duplicates[0].get('id')}")
    for label, candidate_id in GUARDS.items():
        found = [item for item in items if item.get("id") == candidate_id]
        if len(found) != 1 or found[0].get("status") != "pending":
            raise RuntimeError(f"{label} candidate missing or status changed")

    payload = {
        key: candidate[key]
        for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of", "status")
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    intent = {
        "round": "R291", "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(UTC).isoformat(), "preregistration_sha256": prereg_hash,
        "preflight_http_status": status, "preflight_response_sha256": sha(raw),
        "candidate_count_before": len(items), "matches_before": len(duplicates),
        "request_body": payload, "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(proof_paths[0], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    post_status, post_raw, post_error = request("POST", body)
    try:
        post_body = json.loads(post_raw) if post_raw else None
    except json.JSONDecodeError:
        post_body = None
    post_record = {
        "round": "R291", "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status, "transport_error": post_error,
        "response_sha256": sha(post_raw) if post_raw else None, "response_body": post_body,
    }
    post_hash = save_once(proof_paths[1], (json.dumps(post_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    read_status, read_raw, read_error = request("GET")
    try:
        after = json.loads(read_raw).get("items", []) if read_raw else []
    except json.JSONDecodeError:
        after = []
    created_id = post_body.get("id") if isinstance(post_body, dict) else None
    created = [item for item in after if item.get("id") == created_id] if created_id else []
    guards_after = {
        label: [item for item in after if item.get("id") == candidate_id]
        for label, candidate_id in GUARDS.items()
    }
    passed = bool(
        post_status == 200 and read_status == 200 and len(created) == 1
        and created[0].get("status") == "pending"
        and created[0].get("source_id") == candidate["source_id"]
        and created[0].get("data_as_of") == candidate["data_as_of"]
        and all(len(rows) == 1 and rows[0].get("status") == "pending" for rows in guards_after.values())
    )
    proof = {
        "round": "R291", "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "api_base": BASE,
        "preregistration_sha256": prereg_hash, "intent_sha256": intent_hash,
        "post_proof_sha256": post_hash, "post_http_status": post_status,
        "post_transport_error": post_error, "readback_http_status": read_status,
        "readback_transport_error": read_error, "readback_response_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(items), "candidate_count_after": len(after),
        "candidate": created[0] if len(created) == 1 else None,
        "guard_candidates": {label: rows[0] if len(rows) == 1 else None for label, rows in guards_after.items()},
        "registration_pass": passed,
    }
    proof_hash = save_once(proof_paths[2], (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({
        "registration_pass": passed, "candidate_id": created_id,
        "post_http_status": post_status, "readback_http_status": read_status,
        "candidate_count_before": len(items), "candidate_count_after": len(after),
        "proof_sha256": proof_hash, "transport_error": post_error or read_error,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
