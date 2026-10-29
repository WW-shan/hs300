from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018"
PRE = EVIDENCE / "r298_preregistration.json"
MANIFEST = EVIDENCE / "r298_input_manifest.jsonl"
OUTPUTS = (
    EVIDENCE / "r298_panel_registration_intent.json",
    EVIDENCE / "r298_panel_registration_post.json",
    EVIDENCE / "r298_panel_registration.json",
)
GUARDS = {
    "R288": ("280b43c9d2364b5589d9d0edc73d1144", "pending"),
    "R289": ("58dc4329c85e4df88271aa9b42276089", "pending"),
    "R290": ("2451e5a386d5489584e4121f42d97792", "pending"),
    "R291": ("4b5134a2f8a1441d8034dbb484b8e238", "pending"),
    "R292": ("cd6f5bc7773b44c9bedb69430f363220", "pending"),
    "R293": ("dbc1248434fb457f8312eadf47d459da", "pending"),
    "R294": ("4df004f0146b4e818f1f8ac16b31ebde", "pending"),
    "R295": ("51aefb61873a470a99abf7f66a614dfe", "pending"),
    "R296": ("48ca7b2f1105431d97c148e3e0574f4c", "pending"),
    "R297": ("25d66a13bc214247a93c233b2a32a7cd", "pending"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha(payload)
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
    config = item.get("config", {})
    expected = candidate["config"]
    return (
        item.get("kind") == candidate["kind"]
        and item.get("name") == candidate["name"]
        and item.get("source_id") == candidate["source_id"]
        and config.get("start") == expected["start"]
        and config.get("end") == expected["end"]
    )


def main() -> int:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R298 proof exists; refusing duplicate POST: {path.name}")

    prereg_bytes = PRE.read_bytes()
    prereg_hash = sha(prereg_bytes)
    sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != sidecar:
        raise RuntimeError("R298 preregistration checksum mismatch")
    prereg = json.loads(prereg_bytes)
    manifest_hash = sha(MANIFEST.read_bytes())
    if manifest_hash != prereg.get("input_manifest_sha256"):
        raise RuntimeError("R298 manifest checksum mismatch")
    addendum_path = EVIDENCE / "r298_manifest_execution_addendum.json"
    if addendum_path.exists():
        addendum_hash = sha(addendum_path.read_bytes())
        addendum_sidecar = addendum_path.with_name(addendum_path.name + ".sha256").read_text(encoding="ascii").split()[0]
        addendum = json.loads(addendum_path.read_text(encoding="utf-8"))
        if addendum_hash != addendum_sidecar or addendum.get("parent_manifest_sha256") != manifest_hash:
            raise RuntimeError("R298 execution addendum checksum/parent mismatch")

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"R298 preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    candidate = prereg["panel_candidate"]
    if any(matches(item, candidate) for item in items):
        raise RuntimeError("Matching R298 candidate already exists; refusing duplicate POST")
    guards_before = {}
    for label, (candidate_id, expected_status) in GUARDS.items():
        found = [item for item in items if item.get("id") == candidate_id]
        if len(found) != 1 or found[0].get("status") != expected_status:
            raise RuntimeError(f"Guard candidate {label} missing or changed")
        guards_before[label] = found[0]

    payload = {key: candidate[key] for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of", "status")}
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R298",
        "classification": "single_panel_registration_intent",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash,
        "input_manifest_sha256": manifest_hash,
        "manifest_execution_addendum_sha256": sha(addendum_path.read_bytes()) if addendum_path.exists() else None,
        "api_base": API,
        "preflight_http_status": status,
        "preflight_response_sha256": sha(raw),
        "candidate_count_before": len(items),
        "guard_candidates_before": guards_before,
        "request_body": payload,
        "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    write_once(OUTPUTS[0], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    post_status, post_raw, post_error = request("POST", body)
    post_proof = {
        "round": "R298",
        "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
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
    guards_after = {
        label: next((item for item in read_items if item.get("id") == candidate_id), None)
        for label, (candidate_id, _expected) in GUARDS.items()
    }
    passed = (
        post_status == 200 and read_status == 200 and len(registered) == 1
        and registered[0].get("status") == "pending" and len(read_items) == len(items) + 1
        and all(guards_after[label] is not None and guards_after[label].get("status") == expected_status
                for label, (_candidate_id, expected_status) in GUARDS.items())
    )
    result = {
        "round": "R298",
        "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": API,
        "preregistration_sha256": prereg_hash,
        "input_manifest_sha256": manifest_hash,
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
        "guard_candidates_after": guards_after,
        "registration_pass": passed,
    }
    write_once(OUTPUTS[2], (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
