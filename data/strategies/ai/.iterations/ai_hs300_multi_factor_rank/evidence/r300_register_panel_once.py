from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018/api/backtest/candidates"
PREREG = EVIDENCE / "r300_preregistration.json"
MANIFEST = EVIDENCE / "r300_input_manifest.jsonl"
AMENDMENT = EVIDENCE / "r300_panel_protocol_amendment.json"
INTENT = EVIDENCE / "r300_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r300_panel_registration_post.json"
READBACK = EVIDENCE / "r300_panel_readback.json"
ARTIFACTS = (INTENT, POST_PROOF, READBACK)
REQUEST_BODY = {
    "kind": "strategy",
    "name": "R300 R299分段收益集中度诊断",
    "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "config": {
        "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "parent_round": "R299",
        "parent_candidate_id": "2d19f0651b3948bab8cd30c85e7c4e1a",
        "segment_start_dates": ["2023-05-04", "2025-01-02"],
        "segment_end_dates": ["2024-12-31", "2026-10-16"],
        "analysis": "closed_trade_net_pnl_symbol_concentration_diagnostic",
        "thresholds_changed": False,
    },
    "metrics": {},
    "data_as_of": "2026-10-16",
    "status": "pending",
}
GUARDS = {
    "R288": "280b43c9d2364b5589d9d0edc73d1144",
    "R289": "58dc4329c85e4df88271aa9b42276089",
    "R290": "2451e5a386d5489584e4121f42d97792",
    "R291": "4b5134a2f8a1441d8034dbb484b8e238",
    "R292": "cd6f5bc7773b44c9bedb69430f363220",
    "R293": "dbc1248434fb457f8312eadf47d459da",
    "R294": "4df004f0146b4e818f1f8ac16b31ebde",
    "R295": "51aefb61873a470a99abf7f66a614dfe",
    "R296": "48ca7b2f1105431d97c148e3e0574f4c",
    "R297": "25d66a13bc214247a93c233b2a32a7cd",
    "R298": "01aae1c6f0314612bec472bdd3140a69",
    "R299": "2d19f0651b3948bab8cd30c85e7c4e1a",
    "R301": "f75cfc9b919c4b769bd374765ea7dab3",
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    value = digest(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


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


def matches(item: dict) -> bool:
    cfg = item.get("config") or {}
    expected = REQUEST_BODY["config"]
    return (
        item.get("kind") == REQUEST_BODY["kind"]
        and item.get("name") == REQUEST_BODY["name"]
        and item.get("source_id") == REQUEST_BODY["source_id"]
        and cfg.get("parent_round") == expected["parent_round"]
        and cfg.get("parent_candidate_id") == expected["parent_candidate_id"]
        and cfg.get("segment_start_dates") == expected["segment_start_dates"]
        and cfg.get("segment_end_dates") == expected["segment_end_dates"]
    )


def main() -> None:
    for path in ARTIFACTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R300 panel proof exists; refusing duplicate POST: {path.name}")

    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    prereg_hash = digest(PREREG.read_bytes())
    manifest_raw = MANIFEST.read_bytes()
    manifest_hash = digest(manifest_raw)
    if amendment.get("parent_preregistration_sha256") != prereg_hash:
        raise RuntimeError("R300 amendment does not bind to frozen preregistration")
    if amendment.get("input_manifest_sha256") != manifest_hash:
        raise RuntimeError("R300 amendment does not bind to frozen manifest")
    if amendment.get("registration_script_sha256") != digest(Path(__file__).read_bytes()):
        raise RuntimeError("R300 registration script differs from frozen amendment")

    records = [json.loads(line) for line in manifest_raw.decode("utf-8").splitlines() if line]
    discrepancies = []
    for record in records:
        path = ROOT / record["path"]
        if not path.is_file():
            discrepancies.append({"path": record["path"], "reason": "missing"})
            continue
        st = path.stat()
        actual_hash = digest(path.read_bytes())
        if (st.st_size, st.st_mtime_ns, actual_hash) != (record["bytes"], record["mtime_ns"], record["sha256"]):
            discrepancies.append({"path": record["path"], "bytes": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": actual_hash})
    if discrepancies:
        raise RuntimeError("R300 input manifest mismatch: " + json.dumps(discrepancies, ensure_ascii=False))

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"R300 panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    if len(items) >= 200:
        raise RuntimeError("Research candidate store at capacity; refusing registration")
    for label, candidate_id in GUARDS.items():
        found = [x for x in items if x.get("id") == candidate_id]
        if len(found) != 1 or found[0].get("status") != "pending":
            raise RuntimeError(f"Protected candidate {label} is missing or not pending")
    if any(matches(item) for item in items):
        raise RuntimeError("Matching R300 candidate exists; refusing duplicate POST")

    baseline = {item["id"]: item.get("status") for item in items}
    intent = {
        "round": "R300", "classification": "single_panel_registration_intent_before_outcome_analysis",
        "captured_at_utc": datetime.now(UTC).isoformat(), "endpoint": API,
        "preflight_http_status": status, "preflight_response_sha256": digest(raw),
        "candidate_count_before": len(items), "candidate_ids_and_statuses_before": baseline,
        "protected_rounds": list(GUARDS), "matching_r300_before": 0,
        "preregistration_sha256": prereg_hash, "input_manifest_sha256": manifest_hash,
        "registration_script_sha256": amendment["registration_script_sha256"],
        "request_body": REQUEST_BODY,
        "input_integrity": {"manifest_count": len(records), "verified_count": len(records), "discrepancy_count": 0},
        "retry_policy": "never retry after dispatch",
    }
    write_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    body = json.dumps(REQUEST_BODY, ensure_ascii=False, separators=(",", ":")).encode()
    post_status, post_raw, post_error = request("POST", body)  # Exactly one attempt; never retry.
    proof = {
        "round": "R300", "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "http_status": post_status,
        "transport_error": post_error, "request_body_sha256": digest(body),
        "response_sha256": digest(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw and post_status == 200 else None,
        "response_text": post_raw.decode("utf-8", "replace") if post_raw and post_status != 200 else None,
        "retry_policy": "not retried",
    }
    write_once(POST_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    get_status, get_raw, get_error = request("GET")
    read_items = json.loads(get_raw).get("items", []) if get_raw and get_status == 200 else []
    found = [item for item in read_items if matches(item)]
    unchanged = all(
        len([x for x in read_items if x.get("id") == candidate_id]) == 1
        and next(x for x in read_items if x.get("id") == candidate_id).get("status") == prior
        for candidate_id, prior in baseline.items()
    )
    readback = {
        "round": "R300", "classification": "single_panel_registration_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "http_status": get_status,
        "transport_error": get_error, "response_sha256": digest(get_raw) if get_raw else None,
        "candidate_count_before": len(items), "candidate_count_after": len(read_items),
        "r300_match_count": len(found), "r300_candidate": found[0] if len(found) == 1 else None,
        "protected_statuses_unchanged": unchanged,
        "protected_round_statuses": {label: next((x.get("status") for x in read_items if x.get("id") == cid), None) for label, cid in GUARDS.items()},
        "response": json.loads(get_raw) if get_raw and get_status == 200 else None,
        "all_pass": get_status == 200 and len(found) == 1 and unchanged and post_status == 200,
    }
    write_once(READBACK, (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({k: readback[k] for k in ("http_status", "candidate_count_before", "candidate_count_after", "r300_match_count", "r300_candidate", "protected_statuses_unchanged", "all_pass")}, ensure_ascii=False, indent=2))
    if not readback["all_pass"]:
        raise RuntimeError("R300 panel proof failed; evidence retained and no retry will be made")


if __name__ == "__main__":
    main()
