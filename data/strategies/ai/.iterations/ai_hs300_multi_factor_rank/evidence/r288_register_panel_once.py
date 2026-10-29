"""Register the frozen R288 diagnostic candidate exactly once."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r288_preregistration.json"
INTENT = EVIDENCE / "r288_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r288_panel_registration_post.json"
READBACK_PROOF = EVIDENCE / "r288_panel_registration.json"
BASE = "http://127.0.0.1:3018"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha(path.read_bytes())


def save_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    value = sha_file(path)
    with path.with_name(path.name + ".sha256").open(
        "x", encoding="ascii", newline="\n"
    ) as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def request(url: str, method: str, body: bytes | None = None):
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", f"{type(error).__name__}: {error}"


def main() -> int:
    for path in (INTENT, POST_PROOF, READBACK_PROOF):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R288 registration evidence exists; refusing duplicate POST: {path.name}")
    prereg_hash = sha_file(PRE)
    sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sidecar != prereg_hash:
        raise RuntimeError("R288 preregistration sidecar mismatch")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    if prereg.get("round") != "R288" or prereg.get("production_status") != "pause_production":
        raise RuntimeError("R288 preregistration identity/status check failed")
    candidate = prereg["panel_candidate"]
    status, listing_raw, list_error = request(BASE + "/api/backtest/candidates", "GET")
    if status != 200 or list_error:
        raise RuntimeError(f"Panel preflight GET failed: status={status}, error={list_error}")
    listing = json.loads(listing_raw)
    existing = [
        row for row in listing.get("items", [])
        if row.get("source_id") == candidate["source_id"]
        and row.get("name") == candidate["name"]
        and row.get("config", {}).get("start") == candidate["start"]
        and row.get("config", {}).get("end") == candidate["end"]
    ]
    if existing:
        raise RuntimeError(f"Matching R288 panel candidate already exists: {existing[0].get('id')}")

    body_payload = {
        "kind": candidate["kind"], "name": candidate["name"],
        "source_id": candidate["source_id"],
        "config": {
            "strategy_id": candidate["strategy_id"],
            "start": candidate["start"], "end": candidate["end"],
            "params": {}, "overrides": {}, "matching": "open_t+1",
            "fees_pct": 0.0006, "commission_pct": 0.0006,
            "stamp_tax_pct": 0.001, "slippage_bps": 15.0,
            "max_positions": 10, "max_exposure_pct": 1.0,
            "initial_capital": 1000000.0, "position_sizing": "equal",
            "mode": "position", "holding_days": 35,
            "asset_type": "stock", "minute_fill": False,
        },
        "metrics": {}, "data_as_of": candidate["end"], "status": "pending",
    }
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R288", "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_hash,
        "preflight_http_status": status, "preflight_response_body_sha256": sha(listing_raw),
        "preflight_candidate_count": len(listing.get("items", [])),
        "matching_candidates_before_post": len(existing),
        "request_body": body_payload, "request_body_sha256": sha(body),
        "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(
        INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    post_status, raw, post_error = request(BASE + "/api/backtest/candidates", "POST", body)
    try:
        created = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        created = None
    post_record = {
        "round": "R288", "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": post_status, "transport_error": post_error,
        "response_body_sha256": sha(raw) if raw else None, "response_body": created,
    }
    post_hash = save_once(
        POST_PROOF, (json.dumps(post_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    get_status, get_raw, get_error = request(BASE + "/api/backtest/candidates", "GET")
    try:
        readback = json.loads(get_raw) if get_raw else {}
    except json.JSONDecodeError:
        readback = {}
    candidate_id = created.get("id") if isinstance(created, dict) else None
    matches = [row for row in readback.get("items", []) if row.get("id") == candidate_id]
    proof = {
        "round": "R288", "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(UTC).isoformat(), "api_base": BASE,
        "preregistration_sha256": prereg_hash, "registration_intent_sha256": intent_hash,
        "post_proof_sha256": post_hash, "post_http_status": post_status,
        "post_transport_error": post_error, "readback_http_status": get_status,
        "readback_transport_error": get_error,
        "candidate_count": len(readback.get("items", [])),
        "candidate": matches[0] if matches else None,
        "matching_candidate_count": len(matches),
        "registration_pass": bool(
            post_status == 200 and get_status == 200 and candidate_id and len(matches) == 1
            and matches[0].get("status") == "pending"
            and matches[0].get("source_id") == candidate["source_id"]
        ),
    }
    proof_hash = save_once(
        READBACK_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    print(json.dumps({
        "registration_pass": proof["registration_pass"],
        "candidate_id": candidate_id, "post_http_status": post_status,
        "readback_http_status": get_status, "proof_sha256": proof_hash,
        "transport_error": post_error or get_error,
    }, ensure_ascii=False, indent=2))
    return 0 if proof["registration_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
