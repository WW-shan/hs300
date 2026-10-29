from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A8.json"
PANEL_PROOF = EVIDENCE / "r304_A7_panel_final_readback.json"
URL = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id=12589"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def main() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    panel = json.loads(PANEL_PROOF.read_text(encoding="utf-8"))
    if amendment.get("amendment_id") != "R304-A8" or not panel.get("pending_unique"):
        raise RuntimeError("A8 amendment or prior unique pending panel proof is invalid")
    if amendment.get("fixed_target", {}).get("official_api_url") != URL:
        raise RuntimeError("A8 fixed URL mismatch")

    intent_path = EVIDENCE / "r304_A8_dispatch.json"
    raw_path = EVIDENCE / "r304_A8_detail_response.raw"
    result_path = EVIDENCE / "r304_A8_detail_response.json"
    for path in (intent_path, raw_path, result_path):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A8 evidence exists; refusing duplicate request or overwrite: {path.name}")

    intent = {
        "round": "R304-A8",
        "target_id": "12589",
        "url": URL,
        "dispatch_intent_at_utc": datetime.now(UTC).isoformat(),
        "amendment_sha256": sha(AMENDMENT.read_bytes()),
        "previous_panel_readback_sha256": sha(PANEL_PROOF.read_bytes()),
        "request_count": 0,
        "retry_policy": "never retry after dispatch",
    }
    write_once(intent_path, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    request = urllib.request.Request(URL, headers={
        "Accept": "application/json, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.csindex.com.cn",
        "Referer": "https://www.csindex.com.cn/",
        "User-Agent": "Mozilla/5.0",
    })
    status = None
    body = b""
    headers: dict[str, str] = {}
    error = None
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            body = response.read()
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
        headers = dict(exc.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    try:
        payload = json.loads(body.decode("utf-8")) if body else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    result = {
        "round": "R304-A8",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "request_count": 1,
        "retry_count": 0,
        "url": URL,
        "http_status": status,
        "transport_error": error,
        "response_bytes": len(body),
        "response_sha256": sha(body) if body else None,
        "response_headers": headers,
        "parsed_response": payload,
        "classification": "official_detail_response_captured_once_no_retry",
    }
    write_once(raw_path, body)
    write_once(result_path, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("http_status", "transport_error", "response_bytes", "response_sha256", "request_count", "retry_count")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
