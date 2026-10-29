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
MANIFEST = EVIDENCE / "r302_input_manifest.jsonl"
AMENDMENT = EVIDENCE / "r302_registration_amendment.json"
PREREG = EVIDENCE / "r302_preregistration.json"
INTENT = EVIDENCE / "r302_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r302_panel_registration_post.json"
READBACK = EVIDENCE / "r302_panel_readback.json"
OUTPUTS = (INTENT, POST_PROOF, READBACK)
BODY = {
    "kind": "strategy",
    "name": "R302 固定样本分段集中度核验（非新增回测）",
    "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
    "config": {
        "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "start": "2023-05-04",
        "end": "2026-10-16",
        "matching": "open_t+1",
        "fees_pct": 0.0006,
        "commission_pct": 0.0012,
        "stamp_tax_pct": 0.001,
        "slippage_bps": 15.0,
        "max_positions": 10,
        "max_exposure_pct": 1.0,
        "initial_capital": 1000000.0,
        "position_sizing": "equal",
        "mode": "position",
        "holding_days": 20,
        "asset_type": "stock",
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


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    value = sha(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")


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
    return (
        item.get("kind") == BODY["kind"]
        and item.get("name") == BODY["name"]
        and item.get("source_id") == BODY["source_id"]
        and item.get("config") == BODY["config"]
        and item.get("metrics") == BODY["metrics"]
        and item.get("data_as_of") == BODY["data_as_of"]
    )


def main() -> None:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R302 proof exists; refusing a duplicate POST: {path.name}")
    prereg_hash = sha(PREREG.read_bytes())
    manifest_raw = MANIFEST.read_bytes()
    manifest_hash = sha(manifest_raw)
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    if amendment.get("preregistration_sha256") != prereg_hash:
        raise RuntimeError("R302 amendment/preregistration hash mismatch")
    if amendment.get("input_manifest_sha256") != manifest_hash:
        raise RuntimeError("R302 amendment/input manifest hash mismatch")
    if amendment.get("registration_script_sha256") != sha(Path(__file__).read_bytes()):
        raise RuntimeError("R302 registration script differs from frozen amendment")

    records = [json.loads(line) for line in manifest_raw.decode("utf-8").splitlines() if line]
    discrepancies = []
    for row in records:
        path = ROOT / row["path"]
        if not path.is_file():
            discrepancies.append({"path": row["path"], "reason": "missing"})
            continue
        stat = path.stat()
        actual = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": sha(path.read_bytes())}
        expected = {key: row[key] for key in actual}
        if actual != expected:
            discrepancies.append({"path": row["path"], "expected": expected, "actual": actual})
    if discrepancies:
        raise RuntimeError("R302 frozen input manifest mismatch: " + json.dumps(discrepancies, ensure_ascii=False))

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"R302 panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    if len(items) >= 200:
        raise RuntimeError("Candidate panel is full; refusing registration")
    for label, candidate_id in GUARDS.items():
        found = [x for x in items if x.get("id") == candidate_id]
        if len(found) != 1 or found[0].get("status") != "pending":
            raise RuntimeError(f"Protected candidate {label} missing or not pending")
    if any(matches(item) for item in items):
        raise RuntimeError("Matching R302 candidate exists; refusing duplicate POST")

    baseline = {x["id"]: x.get("status") for x in items}
    intent = {
        "round": "R302", "classification": "single_panel_registration_intent_before_frozen_trade_aggregation",
        "captured_at_utc": datetime.now(UTC).isoformat(), "endpoint": API,
        "preflight_http_status": status, "preflight_response_sha256": sha(raw),
        "candidate_count_before": len(items), "candidate_ids_and_statuses_before": baseline,
        "protected_rounds": list(GUARDS), "matching_r302_before": 0,
        "preregistration_sha256": prereg_hash, "input_manifest_sha256": manifest_hash,
        "registration_script_sha256": amendment["registration_script_sha256"],
        "request_body": BODY, "verified_manifest_entries": len(records),
        "retry_policy": "never retry after dispatch",
    }
    write_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    post_body = json.dumps(BODY, ensure_ascii=False, separators=(",", ":")).encode()
    post_status, post_raw, post_error = request("POST", post_body)  # Exactly one attempt; never retry.
    post = {
        "round": "R302", "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "http_status": post_status,
        "transport_error": post_error, "request_body_sha256": sha(post_body),
        "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw and post_status == 200 else None,
        "response_text": post_raw.decode("utf-8", "replace") if post_raw and post_status != 200 else None,
        "retry_policy": "not retried",
    }
    write_once(POST_PROOF, (json.dumps(post, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    get_status, get_raw, get_error = request("GET")
    read_items = json.loads(get_raw).get("items", []) if get_raw and get_status == 200 else []
    found = [x for x in read_items if matches(x)]
    unchanged = all(
        len([x for x in read_items if x.get("id") == candidate_id]) == 1
        and next(x for x in read_items if x.get("id") == candidate_id).get("status") == prior
        for candidate_id, prior in baseline.items()
    )
    readback = {
        "round": "R302", "classification": "single_panel_registration_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "http_status": get_status,
        "transport_error": get_error, "response_sha256": sha(get_raw) if get_raw else None,
        "candidate_count_before": len(items), "candidate_count_after": len(read_items),
        "r302_match_count": len(found), "r302_candidate": found[0] if len(found) == 1 else None,
        "protected_statuses_unchanged": unchanged,
        "protected_round_statuses": {label: next((x.get("status") for x in read_items if x.get("id") == cid), None) for label, cid in GUARDS.items()},
        "response": json.loads(get_raw) if get_raw and get_status == 200 else None,
        "all_pass": get_status == 200 and post_status == 200 and len(found) == 1 and unchanged,
    }
    write_once(READBACK, (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({k: readback[k] for k in ("http_status", "candidate_count_before", "candidate_count_after", "r302_match_count", "r302_candidate", "protected_statuses_unchanged", "all_pass")}, ensure_ascii=False, indent=2))
    if not readback["all_pass"]:
        raise RuntimeError("R302 panel registration failed; evidence retained and no retry will be made")


if __name__ == "__main__":
    main()
