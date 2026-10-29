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
PREREG = EVIDENCE / "r303_preregistration.json"
MANIFEST = EVIDENCE / "r303_input_manifest.jsonl"
AUDIT = EVIDENCE / "r303_feasibility_audit.json"
INTENT = EVIDENCE / "r303_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r303_panel_registration_post.json"
READBACK = EVIDENCE / "r303_panel_readback.json"
OUTPUTS = (INTENT, POST_PROOF, READBACK)
BODY = {
    "kind": "factor",
    "name": "R303 嵌套挖掘股票池能力审计（无收益回测）",
    "source_id": "hs300_nested_mining_universe_audit",
    "config": {
        "factor_name": "hs300_nested_mining_universe_audit",
        "start": "2018-01-02",
        "end": "2026-10-27",
        "n_groups": 5,
        "rebalance": "monthly",
        "weight": 1.0,
        "fees_pct": 0.0,
        "slippage_bps": 0.0,
        "asset_type": "stock",
    },
    "metrics": {},
    "data_as_of": "2026-10-29",
    "status": "pending",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    digest = sha(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")


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
    return all(item.get(key) == BODY[key] for key in ("kind", "name", "source_id", "config", "metrics", "data_as_of"))


def main() -> None:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R303 proof exists; refusing duplicate POST: {path.name}")
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    manifest_raw = MANIFEST.read_bytes()
    prereg_raw = PREREG.read_bytes()
    if audit.get("input_manifest_sha256") != sha(manifest_raw):
        raise RuntimeError("R303 frozen input manifest hash mismatch")
    if audit.get("preregistration_sha256") != sha(prereg_raw):
        raise RuntimeError("R303 preregistration hash mismatch")
    if audit.get("registration_script_sha256") != sha(Path(__file__).read_bytes()):
        raise RuntimeError("R303 registration script differs from frozen hash")

    get_status, before_raw, get_error = request("GET")
    before = json.loads(before_raw).get("items", []) if before_raw and get_status == 200 else []
    frozen = audit["panel_baseline"]
    frozen_ids = frozen["ids_and_statuses"]
    before_map = {str(item.get("id")): str(item.get("status")) for item in before}
    preflight_ok = (
        get_status == 200
        and len(before) == frozen["candidate_count"]
        and before_map == frozen_ids
        and sum(matches(item) for item in before) == 0
    )
    intent = {
        "round": "R303",
        "classification": "single_panel_registration_intent_before_any_alpha_search",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": API,
        "preregistration_sha256": sha(prereg_raw),
        "input_manifest_sha256": sha(manifest_raw),
        "registration_script_sha256": sha(Path(__file__).read_bytes()),
        "baseline_http_status": get_status,
        "baseline_response_sha256": sha(before_raw) if before_raw else None,
        "candidate_count_before": len(before),
        "frozen_baseline_matches": preflight_ok,
        "request_body": BODY,
        "retry_policy": "never retry after dispatch",
    }
    write_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    if not preflight_ok:
        raise RuntimeError("panel differs from frozen baseline; no POST dispatched; evidence retained")

    post_status, post_raw, post_error = request("POST", json.dumps(BODY, ensure_ascii=False).encode("utf-8"))
    post_proof = {
        "round": "R303",
        "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "response_sha256": sha(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw else None,
        "retry_policy": "never retry after dispatch, including transport failure",
    }
    write_once(POST_PROOF, (json.dumps(post_proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    read_status, read_raw, read_error = request("GET")
    read_items = json.loads(read_raw).get("items", []) if read_raw and read_status == 200 else []
    found = [item for item in read_items if matches(item)]
    protected_unchanged = all(
        sum(str(item.get("id")) == candidate_id for item in read_items) == 1
        and next(item for item in read_items if str(item.get("id")) == candidate_id).get("status") == status
        for candidate_id, status in frozen_ids.items()
    )
    readback = {
        "round": "R303",
        "classification": "panel_registration_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": read_status,
        "transport_error": read_error,
        "response_sha256": sha(read_raw) if read_raw else None,
        "candidate_count_before": len(before),
        "candidate_count_after": len(read_items),
        "r303_match_count": len(found),
        "r303_candidate": found[0] if len(found) == 1 else None,
        "protected_statuses_unchanged": protected_unchanged,
        "all_pass": read_status == 200 and post_status == 200 and len(found) == 1 and found[0].get("status") == "pending" and len(read_items) == len(before) + 1 and protected_unchanged,
        "response": json.loads(read_raw) if read_raw and read_status == 200 else None,
    }
    write_once(READBACK, (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({key: readback[key] for key in ("http_status", "candidate_count_before", "candidate_count_after", "r303_candidate", "protected_statuses_unchanged", "all_pass")}, ensure_ascii=False, indent=2))
    if not readback["all_pass"]:
        raise RuntimeError("R303 panel verification failed; evidence retained and no retry will be made")


if __name__ == "__main__":
    main()
