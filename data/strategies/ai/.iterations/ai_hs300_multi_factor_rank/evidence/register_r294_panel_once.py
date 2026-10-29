#!/usr/bin/env python3
"""Register frozen R294 once and preserve intent, POST, and readback proof."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:3018"
PRE = EVIDENCE / "r294_preregistration.json"
GUARDS = {
    "R288": "280b43c9d2364b5589d9d0edc73d1144",
    "R289": "58dc4329c85e4df88271aa9b42276089",
    "R290": "2451e5a386d5489584e4121f42d97792",
    "R291": "4b5134a2f8a1441d8034dbb484b8e238",
    "R292": "cd6f5bc7773b44c9bedb69430f363220",
    "R293": "dbc1248434fb457f8312eadf47d459da",
}
PROOFS = [EVIDENCE / f"r294_panel_registration_{part}.json" for part in ("intent", "post")]
PROOFS.append(EVIDENCE / "r294_panel_registration.json")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    value = sha(payload)
    path.with_name(path.name + ".sha256").write_text(f"{value}  {path.name}\n", encoding="ascii")
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
    if any(path.exists() or Path(str(path) + ".sha256").exists() for path in PROOFS):
        raise RuntimeError("R294 panel proof exists; refusing duplicate registration")
    pre_bytes = PRE.read_bytes()
    pre_hash = sha(pre_bytes)
    expected = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if pre_hash != expected:
        raise RuntimeError("R294 preregistration checksum mismatch")
    prereg = json.loads(pre_bytes)
    if prereg.get("round") != "R294" or prereg.get("status") != "frozen_before_probe":
        raise RuntimeError("R294 preregistration identity/status mismatch")

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"Panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    candidate = prereg["panel_candidate"]
    match = lambda item: (
        item.get("name") == candidate["name"]
        and item.get("source_id") == candidate["source_id"]
        and item.get("config", {}).get("start") == candidate["config"]["start"]
        and item.get("config", {}).get("end") == candidate["config"]["end"]
    )
    if any(match(item) for item in items):
        raise RuntimeError("Matching R294 candidate already exists")
    guards = {}
    for label, candidate_id in GUARDS.items():
        found = [item for item in items if item.get("id") == candidate_id]
        if len(found) != 1 or found[0].get("status") != "pending":
            raise RuntimeError(f"{label} candidate missing or status changed")
        guards[label] = found[0]

    payload = {key: candidate[key] for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of", "status")}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R294", "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(UTC).isoformat(), "preregistration_sha256": pre_hash,
        "preflight_http_status": status, "preflight_response_sha256": sha(raw),
        "candidate_count_before": len(items), "guard_candidates_before": guards,
        "request_body": payload, "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    save_once(PROOFS[0], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    post_status, post_raw, post_error = request("POST", body)
    post = {
        "round": "R294", "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "http_status": post_status,
        "transport_error": post_error, "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw else None, "retry_policy": "not retried",
    }
    save_once(PROOFS[1], (json.dumps(post, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    read_status, read_raw, read_error = request("GET")
    read_items = json.loads(read_raw).get("items", []) if read_raw else []
    registered = [item for item in read_items if match(item)]
    guards_after = {label: next((item for item in read_items if item.get("id") == cid), None) for label, cid in GUARDS.items()}
    result = {
        "round": "R294", "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "api_base": BASE,
        "preregistration_sha256": pre_hash, "intent_sha256": sha(PROOFS[0].read_bytes()),
        "post_proof_sha256": sha(PROOFS[1].read_bytes()), "preflight_http_status": status,
        "post_http_status": post_status, "post_transport_error": post_error,
        "readback_http_status": read_status, "readback_transport_error": read_error,
        "readback_response_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(items), "candidate_count_after": len(read_items),
        "candidate": registered[0] if len(registered) == 1 else None,
        "guard_candidates": guards_after,
        "registration_pass": (
            post_status == 200 and read_status == 200 and len(registered) == 1
            and registered[0].get("status") == "pending" and len(read_items) == len(items) + 1
            and all(item and item.get("status") == "pending" for item in guards_after.values())
        ),
    }
    save_once(PROOFS[2], (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["registration_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
