"""Register one pending R287 diagnostic candidate and preserve request proofs."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
PRE = EVIDENCE / "r287_preregistration.json"
INTENT = EVIDENCE / "r287_panel_registration_intent.json"
POST_PROOF = EVIDENCE / "r287_panel_registration_post.json"
READBACK_PROOF = EVIDENCE / "r287_panel_registration.json"
BASE = "http://127.0.0.1:3018"


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha_file(path)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request(url: str, method: str, body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
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
            raise RuntimeError(f"R287 proof exists; refusing a duplicate POST: {path.name}")
    prereg = json.loads(PRE.read_text(encoding="utf-8"))
    pre_hash = sha_file(PRE)
    sidecar = PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg.get("round") != "R287" or sidecar != pre_hash:
        raise RuntimeError("R287 preregistration hash/identity check failed")
    if sha_file(Path(__file__).resolve()) != prereg["registration_script_sha256"]:
        raise RuntimeError("R287 registrar changed after preregistration")

    candidate = prereg["panel_candidate"]
    payload = {
        "kind": candidate["kind"],
        "name": candidate["name"],
        "source_id": candidate["source_id"],
        "config": {
            "strategy_id": candidate["strategy_id"],
            "start": candidate["start"],
            "end": candidate["end"],
            "params": {},
            "overrides": {},
            "matching": "open_t+1",
            "fees_pct": 0.0006,
            "commission_pct": 0.0006,
            "stamp_tax_pct": 0.001,
            "slippage_bps": 15.0,
            "max_positions": 10,
            "max_exposure_pct": 1.0,
            "initial_capital": 1000000.0,
            "position_sizing": "equal",
            "mode": "position",
            "holding_days": 35,
            "asset_type": "stock",
            "minute_fill": False,
        },
        "metrics": {},
        "data_as_of": candidate["end"],
        "status": "pending",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R287",
        "classification": "single_pending_panel_registration_intent",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": pre_hash,
        "request_body": payload,
        "request_body_sha256": sha_bytes(body),
        "retry_policy": "never retry POST after dispatch",
    }
    intent_hash = save_once(INTENT, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    status, raw, error = request(BASE + "/api/backtest/candidates", "POST", body)
    try:
        created = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        created = None
    post = {
        "round": "R287",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "http_status": status,
        "transport_error": error,
        "response_body_sha256": sha_bytes(raw) if raw else None,
        "response_body": created if created is not None else raw.decode("utf-8", errors="replace"),
    }
    post_hash = save_once(POST_PROOF, (json.dumps(post, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    read_status, read_raw, read_error = request(BASE + "/api/backtest/candidates", "GET")
    try:
        listing = json.loads(read_raw) if read_raw else {}
    except json.JSONDecodeError:
        listing = {}
    candidate_id = created.get("id") if isinstance(created, dict) else None
    matches = [row for row in listing.get("items", []) if row.get("id") == candidate_id]
    passed = bool(
        status == 200 and read_status == 200 and candidate_id and len(matches) == 1
        and matches[0].get("status") == "pending"
        and matches[0].get("source_id") == candidate["source_id"]
        and matches[0].get("data_as_of") == candidate["end"]
    )
    proof = {
        "round": "R287",
        "classification": "panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": BASE,
        "preregistration_sha256": pre_hash,
        "registration_intent_sha256": intent_hash,
        "post_proof_sha256": post_hash,
        "post_http_status": status,
        "post_transport_error": error,
        "readback_http_status": read_status,
        "readback_transport_error": read_error,
        "candidate_count": len(listing.get("items", [])),
        "candidate": matches[0] if len(matches) == 1 else None,
        "registration_pass": passed,
    }
    proof_hash = save_once(READBACK_PROOF, (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({
        "registration_pass": passed,
        "candidate_id": candidate_id,
        "post_http_status": status,
        "readback_http_status": read_status,
        "proof_sha256": proof_hash,
        "transport_error": error or read_error,
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
