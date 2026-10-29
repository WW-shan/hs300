from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A11.json"
BASELINE = EVIDENCE / "r304_A9_panel_readback.json"
URL = "http://127.0.0.1:3018/api/backtest/candidates"
R304_ID = "1ed2c75285b74fc6a521cc73f75f7320"
PROTECTED = {
    "c0833d6f5a97464d9fc6653319ce49c2": "pending",
    "f438e8b03baa464d9bf7b44d891580c9": "pending",
    "f75cfc9b919c4b769bd374765ea7dab3": "pending",
}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def main() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if amendment.get("amendment_id") != "R304-A11" or sha(BASELINE.read_bytes()) != amendment["panel"]["baseline_readback_sha256"]:
        raise RuntimeError("A11 amendment or baseline hash mismatch")
    for name in ("r304_A11_panel_readback.raw", "r304_A11_panel_readback.json"):
        path = EVIDENCE / name
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A11 evidence exists; refusing duplicate GET or overwrite: {name}")

    request = urllib.request.Request(URL, headers={"Accept": "application/json"}, method="GET")
    status = None
    body = b""
    error = None
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    payload = json.loads(body) if body and status == 200 else None
    items = payload.get("items", []) if isinstance(payload, dict) else []
    target = [item for item in items if str(item.get("id")) == R304_ID]
    protected = {
        item_id: [str(item.get("status")) for item in items if str(item.get("id")) == item_id]
        for item_id in PROTECTED
    }
    protected_ok = all(values == [expected] for (item_id, expected), values in zip(PROTECTED.items(), protected.values()))
    baseline_items = baseline.get("response", {}).get("items", [])
    baseline_status = {str(item.get("id")): item.get("status") for item in baseline_items}
    current_status = {str(item.get("id")): item.get("status") for item in items}
    all_existing_statuses_unchanged = all(current_status.get(item_id) == state for item_id, state in baseline_status.items())
    proof = {
        "round": "R304-A11",
        "classification": "read_only_final_panel_readback",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "endpoint": URL,
        "http_status": status,
        "transport_error": error,
        "response_sha256": sha(body) if body else None,
        "candidate_count": len(items),
        "r304_match_count": len(target),
        "r304_candidate_status": target[0].get("status") if len(target) == 1 else None,
        "protected_candidate_statuses": protected,
        "protected_statuses_unchanged": protected_ok,
        "all_existing_statuses_unchanged_vs_A9": all_existing_statuses_unchanged,
        "panel_post_count": 0,
        "get_count": 1,
        "all_pass": status == 200 and len(target) == 1 and target[0].get("status") == "pending" and protected_ok and all_existing_statuses_unchanged,
        "response": payload,
    }
    write_once(EVIDENCE / "r304_A11_panel_readback.raw", body)
    encoded = (json.dumps(proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_once(EVIDENCE / "r304_A11_panel_readback.json", encoded)
    print(json.dumps({key: proof[key] for key in ("http_status", "candidate_count", "r304_match_count", "r304_candidate_status", "protected_statuses_unchanged", "all_existing_statuses_unchanged_vs_A9", "panel_post_count", "all_pass")}, ensure_ascii=False, indent=2))
    if not proof["all_pass"]:
        raise RuntimeError("A11 final panel readback failed; no retry will be made")


if __name__ == "__main__":
    main()
