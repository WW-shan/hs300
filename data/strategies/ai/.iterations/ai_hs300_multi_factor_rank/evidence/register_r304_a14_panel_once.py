from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018/api/backtest/candidates"
PLAN = EVIDENCE / "r304_A14_preregistration.json"
BASELINE = EVIDENCE / "r304_A11_panel_readback.json"
A13_PROOF = EVIDENCE / "r304_A13_panel_registration.json"
OUTPUTS = (
    EVIDENCE / "r304_A14_panel_registration_intent.json",
    EVIDENCE / "r304_A14_panel_registration_post.json",
    EVIDENCE / "r304_A14_panel_registration_readback.json",
)
PROTECTED_IDS = {
    "1ed2c75285b74fc6a521cc73f75f7320",
    "ecd1805dde7a4a8d971718116bd773f6",
}
R304_ID = "1ed2c75285b74fc6a521cc73f75f7320"
A13_ID = "ecd1805dde7a4a8d971718116bd773f6"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def request(method: str, body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", f"{type(error).__name__}: {error}"


def candidate_matches(item: dict, candidate: dict) -> bool:
    return all(item.get(key) == candidate.get(key) for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of"))


def main() -> None:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R304-A14 evidence exists; refusing duplicate POST: {path.name}")

    plan_raw = PLAN.read_bytes()
    plan = json.loads(plan_raw)
    candidate = plan["panel"]["candidate"]
    baseline_raw = BASELINE.read_bytes()
    baseline = json.loads(baseline_raw)
    a13_raw = A13_PROOF.read_bytes()
    a13_proof = json.loads(a13_raw)
    if sha(plan_raw) != PLAN.with_name(PLAN.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R304-A14 preregistration checksum mismatch")
    baseline_items = baseline.get("response", {}).get("items", [])
    if baseline.get("http_status") != 200 or len(baseline_items) != baseline.get("candidate_count"):
        raise RuntimeError("R304 A11 frozen panel baseline is invalid")
    baseline_statuses = {str(item["id"]): item.get("status") for item in baseline_items}
    if R304_ID not in baseline_statuses or a13_proof.get("registration_pass") is not True:
        raise RuntimeError("R304 baseline or A13 registration proof is invalid")
    a13 = a13_proof.get("candidate", {})
    a13_id = str(a13.get("id", ""))
    if a13_id != A13_ID or a13.get("status") != "pending":
        raise RuntimeError("A13 proof does not contain the expected unique pending candidate")
    expected_statuses = {**baseline_statuses, A13_ID: "pending"}

    pre_status, pre_raw, pre_error = request("GET")
    items = json.loads(pre_raw).get("items", []) if pre_status == 200 and pre_raw else []
    before_statuses = {str(item.get("id")): item.get("status") for item in items}
    guards = (
        pre_status == 200
        and len(items) == len(expected_statuses)
        and before_statuses == expected_statuses
        and all(sum(str(item.get("id")) == protected for item in items) == 1 for protected in PROTECTED_IDS)
        and all(next(item for item in items if str(item.get("id")) == protected).get("status") == "pending" for protected in PROTECTED_IDS)
        and not any(candidate_matches(item, candidate) for item in items)
    )
    intent = {
        "round": "R304-A14",
        "classification": "single_panel_registration_intent_for_posthoc_feasibility_hypothesis",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": API,
        "preregistration_sha256": sha(plan_raw),
        "baseline_file_sha256": sha(baseline_raw),
        "A13_registration_proof_sha256": sha(a13_raw),
        "preflight_http_status": pre_status,
        "preflight_transport_error": pre_error,
        "preflight_response_sha256": sha(pre_raw) if pre_raw else None,
        "candidate_count_before": len(items),
        "existing_statuses_match_frozen_A11_plus_A13": before_statuses == expected_statuses,
        "protected_R304_and_A13_pending": all(sum(str(item.get("id")) == protected for item in items) == 1 and next((item for item in items if str(item.get("id")) == protected), {}).get("status") == "pending" for protected in PROTECTED_IDS),
        "duplicate_candidate_absent": not any(candidate_matches(item, candidate) for item in items),
        "request_body": candidate,
        "post_dispatch_retry": "never",
        "guards_pass": guards,
    }
    write_once(OUTPUTS[0], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if not guards:
        raise RuntimeError("R304-A14 panel preflight failed; no POST sent, intent retained")

    body = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    post_status, post_raw, post_error = request("POST", body)
    post_record = {
        "round": "R304-A14",
        "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw else None,
        "retry": "not retried",
    }
    write_once(OUTPUTS[1], (json.dumps(post_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    read_status, read_raw, read_error = request("GET")
    after = json.loads(read_raw).get("items", []) if read_status == 200 and read_raw else []
    matches = [item for item in after if candidate_matches(item, candidate)]
    after_statuses = {str(item.get("id")): item.get("status") for item in after}
    status_unchanged = all(after_statuses.get(key) == value for key, value in before_statuses.items())
    result = {
        "round": "R304-A14",
        "classification": "panel_registration_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "readback_http_status": read_status,
        "readback_transport_error": read_error,
        "readback_response_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(items),
        "candidate_count_after": len(after),
        "candidate_match_count": len(matches),
        "candidate": matches[0] if len(matches) == 1 else None,
        "R304_and_A13_pending": all(sum(str(item.get("id")) == protected for item in after) == 1 and next((item for item in after if str(item.get("id")) == protected), {}).get("status") == "pending" for protected in PROTECTED_IDS),
        "existing_statuses_unchanged": status_unchanged,
        "all_pass": post_status == 200 and read_status == 200 and len(after) == len(items) + 1 and len(matches) == 1 and matches[0].get("status") == "pending" and status_unchanged,
        "response": json.loads(read_raw) if read_status == 200 and read_raw else None,
    }
    write_once(OUTPUTS[2], (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("readback_http_status", "candidate_count_before", "candidate_count_after", "candidate_match_count", "R304_and_A13_pending", "existing_statuses_unchanged", "all_pass")}, ensure_ascii=False, indent=2))
    if not result["all_pass"]:
        raise RuntimeError("R304-A14 panel verification failed; evidence retained and POST will not be retried")


if __name__ == "__main__":
    main()
