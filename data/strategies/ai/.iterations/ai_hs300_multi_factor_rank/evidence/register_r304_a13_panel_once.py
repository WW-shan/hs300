from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018"
PRE = EVIDENCE / "r304_A13_posthoc_panel_registration.json"
BASELINE = EVIDENCE / "r304_A11_panel_readback.json"
AUDIT = EVIDENCE / "r304_A13_deviation_audit.json"
OUTPUTS = (
    EVIDENCE / "r304_A13_panel_registration_intent.json",
    EVIDENCE / "r304_A13_panel_registration_post.json",
    EVIDENCE / "r304_A13_panel_registration.json",
)
R304_ID = "1ed2c75285b74fc6a521cc73f75f7320"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(raw)
    digest = sha(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request(method: str, body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + "/api/backtest/candidates", data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", f"{type(error).__name__}: {error}"


def matches(item: dict, candidate: dict) -> bool:
    return (
        item.get("kind") == candidate["kind"]
        and item.get("name") == candidate["name"]
        and item.get("source_id") == candidate["source_id"]
        and item.get("config", {}).get("start") == candidate["config"]["start"]
        and item.get("config", {}).get("end") == candidate["config"]["end"]
    )


def main() -> int:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A13 panel evidence exists; refusing duplicate registration: {path.name}")

    pre_raw = PRE.read_bytes()
    baseline_raw = BASELINE.read_bytes()
    audit_raw = AUDIT.read_bytes()
    pre_hash, baseline_hash, audit_hash = sha(pre_raw), sha(baseline_raw), sha(audit_raw)
    pre = json.loads(pre_raw)
    baseline = json.loads(baseline_raw)
    audit = json.loads(audit_raw)
    if pre["record_type"] != "post_hoc_panel_registration_preregistration":
        raise RuntimeError("A13 panel plan does not disclose its post-hoc status")
    if pre_hash != PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("A13 panel plan checksum mismatch")
    if baseline_hash != pre["panel"]["baseline_readback_sha256"]:
        raise RuntimeError("A13 panel baseline checksum mismatch")
    if audit["round"] != "R304-A13" or audit["pit_reconstruction"] != "no-go":
        raise RuntimeError("A13 audit does not match its registered scope")

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"A13 panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    baseline_items = baseline.get("response", {}).get("items", [])
    before_statuses = {str(item["id"]): item.get("status") for item in baseline_items}
    current_statuses = {str(item["id"]): item.get("status") for item in items}
    if len(items) != pre["panel"]["candidate_count_expected_before"]:
        raise RuntimeError(f"Unexpected panel candidate count before POST: {len(items)}")
    if any(current_statuses.get(key) != value for key, value in before_statuses.items()):
        raise RuntimeError("An existing candidate status changed since A11")
    existing_r304 = [item for item in items if str(item.get("id")) == R304_ID]
    if len(existing_r304) != 1 or existing_r304[0].get("status") != "pending":
        raise RuntimeError("Existing R304 candidate is missing, duplicated, or no longer pending")

    candidate = pre["candidate"]
    if any(matches(item, candidate) for item in items):
        raise RuntimeError("Matching A13 panel candidate already exists; refusing duplicate POST")
    payload = {key: candidate[key] for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of", "status")}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R304-A13",
        "classification": "single_panel_registration_intent_for_disclosed_posthoc_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "posthoc_panel_plan_sha256": pre_hash,
        "audit_json_sha256": audit_hash,
        "a11_baseline_sha256": baseline_hash,
        "api_base": API,
        "preflight_http_status": status,
        "preflight_response_sha256": sha(raw),
        "candidate_count_before": len(items),
        "existing_r304_id": R304_ID,
        "existing_r304_status": existing_r304[0]["status"],
        "existing_statuses_unchanged_vs_A11": True,
        "request_body": payload,
        "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    write_once(OUTPUTS[0], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    post_status, post_raw, post_error = request("POST", body)
    post_proof = {
        "round": "R304-A13",
        "classification": "single_panel_registration_post_proof_for_disclosed_posthoc_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw else None,
        "retry_policy": "not retried",
    }
    write_once(OUTPUTS[1], (json.dumps(post_proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    read_status, read_raw, read_error = request("GET")
    read_items = json.loads(read_raw).get("items", []) if read_raw else []
    registered = [item for item in read_items if matches(item, candidate)]
    after_statuses = {str(item["id"]): item.get("status") for item in read_items}
    old_statuses_unchanged = all(after_statuses.get(key) == value for key, value in before_statuses.items())
    existing_r304_after = [item for item in read_items if str(item.get("id")) == R304_ID]
    passed = (
        post_status == 200
        and read_status == 200
        and len(read_items) == len(items) + 1
        and len(registered) == 1
        and registered[0].get("status") == "pending"
        and len(existing_r304_after) == 1
        and existing_r304_after[0].get("status") == "pending"
        and old_statuses_unchanged
    )
    result = {
        "round": "R304-A13",
        "classification": "posthoc_audit_panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "posthoc_panel_plan_sha256": pre_hash,
        "audit_json_sha256": audit_hash,
        "intent_sha256": sha(OUTPUTS[0].read_bytes()),
        "post_proof_sha256": sha(OUTPUTS[1].read_bytes()),
        "preflight_http_status": status,
        "post_http_status": post_status,
        "post_transport_error": post_error,
        "readback_http_status": read_status,
        "readback_transport_error": read_error,
        "readback_response_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(items),
        "candidate_count_after": len(read_items),
        "candidate": registered[0] if len(registered) == 1 else None,
        "existing_r304_candidate": existing_r304_after[0] if len(existing_r304_after) == 1 else None,
        "existing_statuses_unchanged_vs_A11": old_statuses_unchanged,
        "registration_pass": passed,
        "interpretation": "Registration records a disclosed retrospective deviation audit; it does not retroactively preregister the chunk request or establish alpha/PIT feasibility.",
    }
    write_once(OUTPUTS[2], (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
