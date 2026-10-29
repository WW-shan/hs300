from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018/api/backtest/candidates"
PLAN = EVIDENCE / "r307_gap_retention_preregistration.json"
BASELINE = EVIDENCE / "r305_panel_registration_readback.json"
PREFIX = EVIDENCE / "r307_panel_registration"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    with path.open("xb") as stream:
        stream.write(raw)
    with sidecar.open("x", encoding="ascii") as stream:
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


def status_map(items: list[dict]) -> dict[str, str | None]:
    return {str(item.get("id")): item.get("status") for item in items}


def main() -> None:
    suffixes = (
        "_intent.json",
        "_preflight.raw.json",
        "_post.json",
        "_post.raw.json",
        "_readback.json",
        "_readback.raw.json",
    )
    outputs = [PREFIX.with_name(PREFIX.name + suffix) for suffix in suffixes]
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in outputs):
        raise RuntimeError("R307 panel evidence exists; refusing a duplicate POST")

    plan_raw = PLAN.read_bytes()
    plan_hash = sha(plan_raw)
    sidecar = PLAN.with_name(PLAN.name + ".sha256").read_text(encoding="ascii").split()[0]
    if plan_hash != sidecar:
        raise RuntimeError("R307 preregistration checksum mismatch")
    baseline_raw = BASELINE.read_bytes()
    baseline = json.loads(baseline_raw)
    if baseline.get("readback_candidate_count") != 23 or baseline.get("registration_pass") is not True:
        raise RuntimeError("R305 final panel readback is not a passing 23-candidate baseline")
    expected = status_map(baseline.get("response", {}).get("items", []))
    if len(expected) != 23:
        raise RuntimeError("Frozen panel baseline has an unexpected candidate count")
    candidate = json.loads(plan_raw)["panel"]["candidate"]

    pre_status, pre_raw, pre_error = request("GET")
    write_once(PREFIX.with_name(PREFIX.name + "_preflight.raw.json"), pre_raw)
    before = json.loads(pre_raw).get("items", []) if pre_raw else []
    before_statuses = status_map(before)
    duplicate_absent = not any(
        item.get("source_id") == candidate["source_id"] or item.get("name") == candidate["name"]
        for item in before
    )
    protected = json.loads(plan_raw)["panel"]["protected_candidates"]
    protected_ok = all(
        sum(str(item.get("id")) == spec["id"] for item in before) == 1
        and next((item for item in before if str(item.get("id")) == spec["id"]), {}).get("status") == spec["status"]
        for spec in protected.values()
    )
    guards = (
        pre_status == 200
        and len(before) == 23
        and before_statuses == expected
        and duplicate_absent
        and protected_ok
    )
    intent = {
        "round": "R307",
        "classification": "exploratory_conditional_hypothesis_panel_registration_intent",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": plan_hash,
        "baseline_sha256": sha(baseline_raw),
        "preflight_http_status": pre_status,
        "preflight_transport_error": pre_error,
        "preflight_response_sha256": sha(pre_raw) if pre_raw else None,
        "preflight_candidate_count": len(before),
        "existing_status_map_matches_frozen_baseline": before_statuses == expected,
        "protected_candidates_pending": protected_ok,
        "duplicate_source_id_and_name_absent": duplicate_absent,
        "candidate": candidate,
        "post_dispatch_retry": "never",
        "guards_pass": guards,
    }
    write_once(
        PREFIX.with_name(PREFIX.name + "_intent.json"),
        (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    if not guards:
        raise RuntimeError("R307 panel preflight failed; no POST sent; raw GET and intent retained")

    body = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    post_status, post_raw, post_error = request("POST", body)
    write_once(PREFIX.with_name(PREFIX.name + "_post.raw.json"), post_raw)
    post = {
        "round": "R307",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "request_body_sha256": sha(body),
        "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw else None,
        "retry": "not retried",
    }
    write_once(
        PREFIX.with_name(PREFIX.name + "_post.json"),
        (json.dumps(post, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    read_status, read_raw, read_error = request("GET")
    write_once(PREFIX.with_name(PREFIX.name + "_readback.raw.json"), read_raw)
    after = json.loads(read_raw).get("items", []) if read_raw else []
    after_statuses = status_map(after)
    matches = [item for item in after if item.get("source_id") == candidate["source_id"]]
    unchanged = all(after_statuses.get(key) == value for key, value in before_statuses.items())
    passed = (
        read_status == 200
        and len(after) == 24
        and len(matches) == 1
        and matches[0].get("status") == "pending"
        and unchanged
    )
    readback = {
        "round": "R307",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "readback_http_status": read_status,
        "readback_transport_error": read_error,
        "readback_response_sha256": sha(read_raw) if read_raw else None,
        "readback_candidate_count": len(after),
        "candidate_matches": matches,
        "preexisting_statuses_unchanged": unchanged,
        "registration_pass": passed,
        "response": json.loads(read_raw) if read_raw else None,
    }
    write_once(
        PREFIX.with_name(PREFIX.name + "_readback.json"),
        (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "preflight_http_status": pre_status,
        "post_http_status": post_status,
        "readback_http_status": read_status,
        "count_before": len(before),
        "count_after": len(after),
        "new_candidate": matches,
        "existing_statuses_unchanged": unchanged,
        "registration_pass": passed,
    }, ensure_ascii=False, indent=2))
    if not passed:
        raise RuntimeError("R307 registration failed readback; no POST retry")


if __name__ == "__main__":
    main()
