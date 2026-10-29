from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
API = "http://127.0.0.1:3018/api/backtest/candidates"
PRE = EVIDENCE / "r301_preregistration.json"
AMENDMENT = EVIDENCE / "r301_preregistration_amendment.json"
MANIFEST = EVIDENCE / "r301_input_manifest.jsonl"
INTENT = EVIDENCE / "r301_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r301_panel_registration_post.json"
READBACK = EVIDENCE / "r301_panel_readback.json"
OUTPUTS = (INTENT, POST_PROOF, READBACK)

REQUEST_BODY = {
    "kind": "factor",
    "name": "R301 可行性预注册（未生成因子/无回测结果）",
    "source_id": "r301_collection_quality_growth_spread",
    "config": {
        "factor_name": "r301_collection_quality_growth_spread",
        "start": "2022-07-01",
        "end": "2026-10-14",
        "n_groups": 5,
        "rebalance": "monthly",
        "weight": 1.0,
        "fees_pct": 0.0,
        "slippage_bps": 0.0,
        "asset_type": "stock",
    },
    "metrics": {},
    "data_as_of": "2026-10-14",
    "status": "pending",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def document_hash(path: Path) -> str:
    raw = path.read_bytes()
    digest = sha256(raw)
    expected = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
    if digest != expected:
        raise RuntimeError(f"Checksum mismatch: {path.name}")
    return digest


def write_once(path: Path, payload: bytes) -> str:
    digest = sha256(payload)
    with path.open("xb") as stream:
        stream.write(payload)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


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


def candidate_matches(item: dict) -> bool:
    config = item.get("config") or {}
    expected = REQUEST_BODY["config"]
    return (
        item.get("kind") == REQUEST_BODY["kind"]
        and item.get("source_id") == REQUEST_BODY["source_id"]
        and item.get("name") == REQUEST_BODY["name"]
        and config.get("factor_name") == expected["factor_name"]
        and config.get("start") == expected["start"]
        and config.get("end") == expected["end"]
    )


def main() -> None:
    for path in OUTPUTS:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R301 panel proof exists; refusing a duplicate POST: {path.name}")

    preregistration_hash = document_hash(PRE)
    amendment_hash = document_hash(AMENDMENT)
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    if amendment.get("parent_preregistration_sha256") != preregistration_hash:
        raise RuntimeError("R301 amendment does not bind to the frozen parent preregistration")
    manifest_bytes = MANIFEST.read_bytes()
    manifest_hash = sha256(manifest_bytes)
    manifest_sidecar = MANIFEST.with_name(MANIFEST.name + ".sha256").read_text(encoding="ascii").split()[0]
    if manifest_hash != manifest_sidecar:
        raise RuntimeError("R301 manifest checksum mismatch")

    status, raw, error = request("GET")
    if status != 200 or error:
        raise RuntimeError(f"R301 panel preflight failed: HTTP={status}, error={error}")
    items = json.loads(raw).get("items", [])
    if len(items) >= 200:
        raise RuntimeError("Research candidate store is at capacity; refusing panel registration")
    if any(candidate_matches(item) or item.get("source_id") == REQUEST_BODY["source_id"] for item in items):
        raise RuntimeError("A matching R301 source already exists; refusing duplicate registration")
    baseline = {item["id"]: item.get("status") for item in items}
    intent = {
        "round": "R301",
        "classification": "single_feasibility_research_candidate_registration_intent",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "api": API,
        "preflight_http_status": status,
        "preflight_response_sha256": sha256(raw),
        "candidate_count_before": len(items),
        "candidate_ids_and_statuses_before": baseline,
        "matching_r301_candidates_before": 0,
        "preregistration_sha256": preregistration_hash,
        "amendment_sha256": amendment_hash,
        "input_manifest_sha256": manifest_hash,
        "request_body": REQUEST_BODY,
        "interpretation": "Feasibility-only pending panel record; the factor is not built, and no backtest or outcome metric exists.",
        "post_retry_policy": "never retry after dispatch",
    }
    write_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    post_body = json.dumps(REQUEST_BODY, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    post_status, post_raw, post_error = request("POST", post_body)  # Exactly one POST attempt.
    post_proof = {
        "round": "R301",
        "classification": "single_panel_registration_post_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status,
        "transport_error": post_error,
        "request_body_sha256": sha256(post_body),
        "response_sha256": sha256(post_raw) if post_raw else None,
        "response": json.loads(post_raw) if post_raw and post_status == 200 else None,
        "response_text": post_raw.decode("utf-8", "replace") if post_raw and post_status != 200 else None,
        "retry_policy": "not retried",
    }
    write_once(POST_PROOF, (json.dumps(post_proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    get_status, get_raw, get_error = request("GET")
    readback_items = json.loads(get_raw).get("items", []) if get_raw and get_status == 200 else []
    matches = [item for item in readback_items if candidate_matches(item)]
    unchanged = all(
        len([item for item in readback_items if item.get("id") == candidate_id]) == 1
        and next(item for item in readback_items if item.get("id") == candidate_id).get("status") == prior_status
        for candidate_id, prior_status in baseline.items()
    )
    readback = {
        "round": "R301",
        "classification": "single_panel_registration_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": get_status,
        "transport_error": get_error,
        "response_sha256": sha256(get_raw) if get_raw else None,
        "response": json.loads(get_raw) if get_raw and get_status == 200 else None,
        "candidate_count_before": len(items),
        "candidate_count_after": len(readback_items),
        "r301_matches": matches,
        "prior_candidate_statuses_unchanged": unchanged,
        "registration_pass": (
            post_status == 200
            and get_status == 200
            and len(matches) == 1
            and matches[0].get("status") == "pending"
            and matches[0].get("metrics") == {}
            and len(readback_items) == len(items) + 1
            and unchanged
        ),
        "post_retry_policy": "not retried",
    }
    write_once(READBACK, (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    summary = {key: readback[key] for key in ("http_status", "candidate_count_before", "candidate_count_after", "r301_matches", "prior_candidate_statuses_unchanged", "registration_pass")}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if not readback["registration_pass"]:
        raise RuntimeError("R301 panel registration/readback did not pass; POST was not retried")


if __name__ == "__main__":
    main()
