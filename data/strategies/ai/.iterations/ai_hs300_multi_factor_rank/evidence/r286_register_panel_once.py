"""Register the frozen R286 gate activity audit once in the local panel."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r286_preregistration.json"
INTENT = EVIDENCE / "r286_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r286_panel_registration_post.json"
READBACK_PROOF = EVIDENCE / "r286_panel_registration.json"
BASE = "http://127.0.0.1:3018"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request(url: str, method: str, body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except Exception as error:  # Never retry a create after dispatch.
        return None, b"", f"{type(error).__name__}: {error}"


def main() -> int:
    for path in (INTENT, POST_PROOF, READBACK_PROOF):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R286 artifact already exists; refusing duplicate POST: {path.name}")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    prereg_hash = sha_file(PRE)
    sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if sidecar != prereg_hash or prereg.get("round") != "R286":
        raise RuntimeError("R286 preregistration integrity check failed")
    candidate = prereg["panel_candidate"]
    body_payload = {
        "kind": candidate["kind"], "name": candidate["name"],
        "source_id": candidate["source_id"],
        "config": {
            "strategy_id": candidate["strategy_id"], "start": candidate["start"],
            "end": candidate["end"], "params": {}, "overrides": {},
            "matching": "open_t+1", "fees_pct": 0.0006, "commission_pct": 0.0006,
            "stamp_tax_pct": 0.001, "slippage_bps": 15.0, "max_positions": 10,
            "max_exposure_pct": 1.0, "initial_capital": 1000000.0,
            "position_sizing": "equal", "mode": "position",
            "holding_days": 35, "asset_type": "stock", "minute_fill": False,
        },
        "metrics": {}, "data_as_of": candidate["end"], "status": "pending",
    }
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R286", "classification": "single_panel_registration_intent",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_hash, "request_body": body_payload,
        "request_body_sha256": sha_bytes(body), "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    status, raw, error = request(BASE + "/api/backtest/candidates", "POST", body)
    try:
        created = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        created = None
    post_record = {
        "round": "R286", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "http_status": status, "transport_error": error, "response_body_sha256": sha_bytes(raw) if raw else None,
        "response_body": created,
    }
    post_hash = save_once(POST_PROOF, (json.dumps(post_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    get_status, get_raw, get_error = request(BASE + "/api/backtest/candidates", "GET")
    try:
        listing = json.loads(get_raw) if get_raw else {}
    except json.JSONDecodeError:
        listing = {}
    candidate_id = created.get("id") if isinstance(created, dict) else None
    matches = [row for row in listing.get("items", []) if row.get("id") == candidate_id]
    proof = {
        "round": "R286", "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "api_base": BASE,
        "preregistration_sha256": prereg_hash, "registration_intent_sha256": intent_hash,
        "post_proof_sha256": post_hash, "post_http_status": status, "post_transport_error": error,
        "readback_http_status": get_status, "readback_transport_error": get_error,
        "candidate_count": len(listing.get("items", [])), "candidate": matches[0] if matches else None,
        "registration_pass": bool(status == 200 and get_status == 200 and created and matches and matches[0].get("status") == "pending"),
    }
    proof_hash = save_once(READBACK_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({"registration_pass": proof["registration_pass"], "candidate_id": candidate_id,
                      "post_http_status": status, "readback_http_status": get_status,
                      "proof_sha256": proof_hash, "transport_error": error or get_error}, ensure_ascii=False, indent=2))
    return 0 if proof["registration_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
