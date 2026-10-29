"""Register the frozen R285 provenance audit as a panel candidate once."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:3018"
PRE = EVIDENCE / "r285_preregistration.json"
INTENT = EVIDENCE / "r285_panel_registration_intent.json"
POST_EVIDENCE = EVIDENCE / "r285_panel_registration_post.json"
PANEL_PROOF = EVIDENCE / "r285_panel_registration.json"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def save_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = digest_file(path)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def request_once(url: str, method: str, body: bytes | None = None) -> tuple[int | None, bytes, str | None]:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read(), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None
    except Exception as error:  # one attempt only; never retry a dispatched create
        return None, b"", f"{type(error).__name__}: {error}"


def main() -> int:
    for path in (INTENT, POST_EVIDENCE, PANEL_PROOF):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R285 panel artifact exists; refusing duplicate registration: {path.name}")
    prereg_sha = digest_file(PRE)
    if PRE.with_name(PRE.name + ".sha256").read_text(encoding="ascii").split()[0] != prereg_sha:
        raise RuntimeError("R285 preregistration hash mismatch")

    payload = {
        "kind": "strategy",
        "name": "R285 宏观数据时间戳归档可行性审计",
        "source_id": "ai_hs300_macro_gate_quality_r275_remove_term",
        "config": {
            "strategy_id": "ai_hs300_macro_gate_quality_r275_remove_term",
            "start": "2026-04-20",
            "end": "2026-10-16",
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
            "holding_days": 5,
            "asset_type": "stock",
            "minute_fill": False,
        },
        "metrics": {},
        "data_as_of": "2026-10-16",
        "status": "pending",
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    intent = {
        "round": "R285",
        "classification": "one_time_system_panel_candidate_registration",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "preregistration_sha256": prereg_sha,
        "executor_sha256": digest_file(Path(__file__).resolve()),
        "method": "POST /api/backtest/candidates",
        "request_body": payload,
        "request_body_sha256": digest_bytes(body),
        "status": "pending",
        "retry_policy": "never_retry_after_dispatch_intent",
    }
    intent_sha = save_once(
        INTENT,
        (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    status, raw, transport_error = request_once(BASE + "/api/backtest/candidates", "POST", body)
    post_record = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "POST",
        "path": "/api/backtest/candidates",
        "http_status": status,
        "response_bytes": len(raw),
        "response_body_sha256": digest_bytes(raw) if raw else None,
        "response_body_base64": base64.b64encode(raw).decode("ascii") if raw else None,
        "transport_error": transport_error,
    }
    post_sha = save_once(
        POST_EVIDENCE,
        (json.dumps(post_record, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    try:
        created = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        created = None

    # A single read-back proves whether the dispatched registration persisted.
    get_status, get_raw, get_error = request_once(BASE + "/api/backtest/candidates", "GET")
    try:
        listing = json.loads(get_raw) if get_raw else None
    except json.JSONDecodeError:
        listing = None
    item_id = created.get("id") if isinstance(created, dict) else None
    matches = [item for item in (listing or {}).get("items", []) if item.get("id") == item_id]
    panel = {
        "round": "R285",
        "classification": "system_panel_registration_and_readback_proof",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": BASE,
        "preregistration_sha256": prereg_sha,
        "executor_sha256": digest_file(Path(__file__).resolve()),
        "registration_intent_sha256": intent_sha,
        "post_response_evidence_sha256": post_sha,
        "post": {
            "http_status": status,
            "response_body_sha256": digest_bytes(raw) if raw else None,
            "transport_error": transport_error,
            "created_candidate": created,
        },
        "readback": {
            "http_status": get_status,
            "response_body_sha256": digest_bytes(get_raw) if get_raw else None,
            "transport_error": get_error,
            "candidate_count": len((listing or {}).get("items", [])),
            "registered_candidate_found": bool(matches),
            "candidate": matches[0] if matches else None,
        },
        "registration_pass": bool(
            status == 200 and isinstance(created, dict) and matches
            and matches[0].get("status") == "pending"
        ),
    }
    panel_sha = save_once(
        PANEL_PROOF,
        (json.dumps(panel, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "registration_pass": panel["registration_pass"],
        "candidate_id": item_id,
        "post_status": status,
        "readback_status": get_status,
        "panel_proof_sha256": panel_sha,
        "transport_error": transport_error or get_error,
    }, ensure_ascii=False, indent=2))
    return 0 if panel["registration_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
