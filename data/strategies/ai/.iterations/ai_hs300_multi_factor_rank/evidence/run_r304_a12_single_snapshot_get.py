from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A12.json"
URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/info/files/20180227/1519719074537344.xlsx"
BASELINE_SHA256 = "e291b4d9f388279ecf06e0c88c57c8ec47761e1dc9727d136502dcc6ae13b94e"
BASELINE_FILE = EVIDENCE / "r304_A10_attachment_response.raw"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def main() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    target = amendment.get("fixed_target", {})
    if amendment.get("amendment_id") != "R304-A12" or target.get("official_url") != URL:
        raise RuntimeError("A12 fixed target mismatch")
    if sha(BASELINE_FILE.read_bytes()) != BASELINE_SHA256 or target.get("source_workbook_sha256") != BASELINE_SHA256:
        raise RuntimeError("A12 baseline workbook hash mismatch")
    intent_path = EVIDENCE / "r304_A12_dispatch.json"
    raw_path = EVIDENCE / "r304_A12_official_file_response.raw"
    result_path = EVIDENCE / "r304_A12_official_file_response.json"
    for path in (intent_path, raw_path, result_path):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A12 evidence exists; refusing repeat request or overwrite: {path.name}")

    intent = {
        "round": "R304-A12",
        "classification": "single_fixed_official_xlsx_get_intent",
        "dispatch_intent_at_utc": datetime.now(UTC).isoformat(),
        "url": URL,
        "amendment_sha256": sha(AMENDMENT.read_bytes()),
        "previous_body_sha256": BASELINE_SHA256,
        "request_count": 0,
        "retry_policy": "never retry after dispatch",
    }
    write_once(intent_path, (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    request = urllib.request.Request(URL, headers={
        "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, application/octet-stream, */*",
        "Referer": "https://www.csindex.com.cn/",
        "User-Agent": "Mozilla/5.0",
    }, method="GET")
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
        status, body, headers = exc.code, exc.read(), dict(exc.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    digest = sha(body) if body else None
    result = {
        "round": "R304-A12",
        "classification": "single_official_file_response_captured_no_retry",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "request_count": 1,
        "retry_count": 0,
        "url": URL,
        "http_status": status,
        "transport_error": error,
        "response_bytes": len(body),
        "response_sha256": digest,
        "response_matches_A10_bytes": digest == BASELINE_SHA256,
        "response_headers": headers,
        "content_type": headers.get("Content-Type"),
        "interpretation": "Same bytes repeat the previously audited response; this request provides no new independent membership evidence. A different payload requires local workbook date/as-of inspection before any PIT conclusion.",
    }
    write_once(raw_path, body)
    write_once(result_path, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("http_status", "transport_error", "response_bytes", "response_sha256", "response_matches_A10_bytes", "request_count", "retry_count")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
