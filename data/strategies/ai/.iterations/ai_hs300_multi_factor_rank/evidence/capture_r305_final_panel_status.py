from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:3018/api/backtest/candidates"
EXPECTED_COUNT = 23
EXPECTED_ID = "cc701e29d6b1450982d2375dd50ce08b"
OUTPUT = EVIDENCE / "r305_final_panel_status.json"
RAW_OUTPUT = EVIDENCE / "r305_final_panel_status.raw.json"


def write_once(path: Path, raw: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> None:
    for path in (OUTPUT, RAW_OUTPUT):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"Final panel proof exists; refusing overwrite: {path.name}")
    request = urllib.request.Request(URL, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    payload = json.loads(raw) if raw else {}
    items = payload.get("items", [])
    matches = [item for item in items if str(item.get("id")) == EXPECTED_ID]
    statuses = {str(item.get("id")): item.get("status") for item in items}
    prior_readback = json.loads((EVIDENCE / "r305_panel_registration_readback.json").read_text(encoding="utf-8"))
    prior_statuses = {str(item["id"]): item.get("status") for item in prior_readback["response"]["items"]}
    unchanged = all(statuses.get(key) == value for key, value in prior_statuses.items())
    passed = status == 200 and len(items) == EXPECTED_COUNT and len(matches) == 1 and matches[0].get("status") == "pending" and unchanged
    raw_sha = write_once(RAW_OUTPUT, raw)
    record = {
        "round": "R305",
        "classification": "read_only_final_panel_status_check_after_scope_correction",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": status,
        "response_sha256": raw_sha,
        "candidate_count": len(items),
        "R305_matches": matches,
        "all_statuses_match_R305_registration_readback": unchanged,
        "check_pass": passed,
        "response": payload,
    }
    result = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    digest = write_once(OUTPUT, result)
    print(json.dumps({"http_status": status, "candidate_count": len(items), "R305": matches, "all_statuses_unchanged": unchanged, "pass": passed, "proof_sha256": digest}, ensure_ascii=False, indent=2))
    if not passed:
        raise RuntimeError("Final panel status check did not match the frozen R305 readback")


if __name__ == "__main__":
    main()
