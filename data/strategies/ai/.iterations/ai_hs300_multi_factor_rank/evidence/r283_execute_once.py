"""Execute only the two frozen R283 route-smoke requests; never retry or overwrite."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
ROOT = EVIDENCE.parents[5]
PRE = EVIDENCE / "r283_preregistration.json"
PANEL = EVIDENCE / "r283_panel_registration.json"
BASE_URL = "http://127.0.0.1:3018/api/backtest/strategy/run"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json_once(path: Path, value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)
    digest = sha256(path)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def verify_sidecar(path: Path) -> str:
    sidecar = path.with_name(path.name + ".sha256")
    expected = sidecar.read_text(encoding="ascii").split()[0]
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA-256 sidecar mismatch: {path.name}")
    return actual


def main() -> int:
    record_path = EVIDENCE / "r283_execution_record.json"
    intent_path = EVIDENCE / "r283_execution_intent.json"
    response_paths = {
        "candidate": EVIDENCE / "r283_candidate_3x_response.json",
        "control": EVIDENCE / "r283_control_3x_response.json",
    }
    candidate_path = response_paths["candidate"]
    targets = [record_path, intent_path, *(response_paths.values())]
    for path in targets:
        if path == candidate_path and path.exists():
            continue
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"R283 output already exists; no request sent: {path.name}")
    pre = json.loads(PRE.read_text(encoding="utf-8"))
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    pre_sha = verify_sidecar(PRE)
    panel_sha = verify_sidecar(PANEL)
    executor_sha = verify_sidecar(Path(__file__).resolve())
    candidate_response_sha = verify_sidecar(candidate_path)
    if panel.get("preregistration_sha256") != pre_sha:
        raise RuntimeError("panel proof does not reference frozen R283 preregistration")
    if panel.get("candidate", {}).get("research_only") is not True:
        raise RuntimeError("candidate is not registered research-only")
    inputs = pre["frozen_inputs"]
    if sha256(ROOT / "data/strategies/ai/ai_hs300_macro_gate_quality_r275_remove_term.py") != inputs["candidate_source_sha256"]:
        raise RuntimeError("candidate source changed after preregistration")
    saved_candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if (
        saved_candidate.get("http_status") != 200
        or saved_candidate.get("strategy_id") != inputs["candidate_id"]
        or saved_candidate.get("response_body_sha256") is None
        or not isinstance(saved_candidate.get("body_json"), dict)
        or saved_candidate["body_json"].get("error")
    ):
        raise RuntimeError("saved candidate response is incomplete or unsuccessful; control request not sent")
    request_template = {
        "symbols": None,
        "start": inputs["start"],
        "end": inputs["end"],
        "params": {},
        "overrides": {},
        "matching": inputs["matching"],
        "entry_fill": None,
        "exit_fill": None,
        "fees_pct": inputs["fees_pct"],
        "commission_pct": inputs["commission_pct"],
        "stamp_tax_pct": inputs["stamp_tax_pct"],
        "slippage_bps": inputs["slippage_bps"],
        "max_positions": inputs["max_positions"],
        "max_exposure_pct": 1.0,
        "initial_capital": 1_000_000.0,
        "position_sizing": inputs["position_sizing"],
        "mode": inputs["mode"],
        "holding_days": inputs["holding_days"],
        "asset_type": inputs["asset_type"],
        "minute_fill": False,
        "regime_filter": None,
        "hs300": inputs["hs300"],
    }
    requests = []
    for role, strategy_id in (("candidate", inputs["candidate_id"]), ("control", inputs["control_id"])):
        body = {**request_template, "strategy_id": strategy_id}
        requests.append((role, body))

    request_proofs = {
        role: {
            "strategy_id": body["strategy_id"],
            "body": body,
            "body_sha256": hashlib.sha256(
                json.dumps(body, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "provenance": (
                "reconstructed_from_frozen_preregistration; original request body was not archived"
                if role == "candidate"
                else "exact_pending_control_request_body"
            ),
        }
        for role, body in requests
    }
    intent = {
        "classification": "R283_one_time_dispatch_intent",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "executor_sha256": executor_sha,
        "preregistration_sha256": pre_sha,
        "panel_registration_sha256": panel_sha,
        "endpoint": BASE_URL,
        "requests": request_proofs,
        "dispatch_state": "control_request_may_be_sent_once_after_this_record",
        "retry_policy": "never_retry_even_if_process_stops_after_dispatch_intent",
    }
    intent_sha = save_json_once(intent_path, intent)

    record = {
        "classification": "R283_exactly_once_system_backtest_route_smoke",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "executor_sha256": executor_sha,
        "preregistration_sha256": pre_sha,
        "panel_registration_sha256": panel_sha,
        "endpoint": BASE_URL,
        "requests": request_proofs,
        "execution_intent": {
            "evidence_file": intent_path.name,
            "evidence_file_sha256": intent_sha,
        },
        "responses": {
            "candidate": {
                "http_status": saved_candidate.get("http_status"),
                "response_body_sha256": saved_candidate.get("response_body_sha256"),
                "evidence_file": candidate_path.name,
                "evidence_file_sha256": candidate_response_sha,
                "transport_error": saved_candidate.get("transport_error"),
                "reused_saved_response_after_executor_reporting_error": True,
            }
        },
        "stop_reason": None,
        "no_retry_policy": True,
    }
    for role, body in requests[1:]:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            BASE_URL,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        status: int | None = None
        headers: dict[str, str] = {}
        raw = b""
        transport_error = None
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                status = response.status
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() in {"content-type", "content-length", "date"}
                }
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            headers = {
                key: value
                for key, value in error.headers.items()
                if key.lower() in {"content-type", "content-length", "date"}
            }
            raw = error.read()
        except Exception as error:  # noqa: BLE001 - capture one transport attempt; never retry
            transport_error = f"{type(error).__name__}: {error}"

        digest = hashlib.sha256(raw).hexdigest() if raw else None
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = None
        response_record = {
            "role": role,
            "strategy_id": body["strategy_id"],
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "http_status": status,
            "headers": headers,
            "response_bytes": len(raw),
            "response_body_sha256": digest,
            "response_body_base64": base64.b64encode(raw).decode("ascii") if raw else None,
            "body_json": parsed,
            "body_text_if_not_json": None if parsed is not None or not raw else raw.decode("utf-8", errors="replace"),
            "transport_error": transport_error,
        }
        response_sha = save_json_once(response_paths[role], response_record)
        record["responses"][role] = {
            "http_status": status,
            "response_body_sha256": digest,
            "evidence_file": response_paths[role].name,
            "evidence_file_sha256": response_sha,
            "transport_error": transport_error,
        }
        print(json.dumps({
            "role": role,
            "strategy_id": body["strategy_id"],
            "http_status": status,
            "response_body_sha256": digest,
            "evidence_file": response_paths[role].name,
            "response_bytes": len(raw),
            "transport_error": transport_error,
            "result_error": parsed.get("error") if isinstance(parsed, dict) else None,
        }, ensure_ascii=False), flush=True)
        if (
            transport_error
            or status != 200
            or not isinstance(parsed, dict)
            or parsed.get("error")
        ):
            record["stop_reason"] = f"{role} request did not complete successfully; second request not sent"
            break
    record["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    record_sha = save_json_once(record_path, record)
    print(json.dumps({
        "execution_record": record_path.name,
        "execution_record_sha256": record_sha,
        "requests_sent": list(record["responses"]),
        "stop_reason": record["stop_reason"],
    }, ensure_ascii=False, indent=2))
    return 0 if record["stop_reason"] is None and len(record["responses"]) == 2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
