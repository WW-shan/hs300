from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A10.json"
URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/info/files/20180227/1519719074537344.xlsx"
SOURCE_RAW = EVIDENCE / "r304_A9_detail_response.raw"
SOURCE_SHA256 = "783193e914444ddf9246baa549dd5cff85e2c634fd60c73795086060ae40ac4f"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def main() -> None:
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    if amendment.get("amendment_id") != "R304-A10":
        raise RuntimeError("A10 amendment id mismatch")
    target = amendment.get("fixed_target", {})
    if target.get("official_url") != URL or target.get("source_response_sha256") != SOURCE_SHA256:
        raise RuntimeError("A10 fixed target mismatch")
    if sha(SOURCE_RAW.read_bytes()) != SOURCE_SHA256:
        raise RuntimeError("A10 source response hash mismatch")

    intent_path = EVIDENCE / "r304_A10_dispatch.json"
    raw_path = EVIDENCE / "r304_A10_attachment_response.raw"
    result_path = EVIDENCE / "r304_A10_attachment_response.json"
    for path in (intent_path, raw_path, result_path):
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A10 evidence exists; refusing repeat request or overwrite: {path.name}")

    intent = {
        "round": "R304-A10",
        "classification": "single_official_attachment_get_dispatch_intent",
        "dispatch_intent_at_utc": datetime.now(UTC).isoformat(),
        "notice_id": "12590",
        "url": URL,
        "source_response_sha256": SOURCE_SHA256,
        "amendment_sha256": sha(AMENDMENT.read_bytes()),
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
        status = exc.code
        body = exc.read()
        headers = dict(exc.headers.items())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"

    result = {
        "round": "R304-A10",
        "classification": "official_attachment_response_captured_once_no_retry",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "request_count": 1,
        "retry_count": 0,
        "url": URL,
        "http_status": status,
        "transport_error": error,
        "response_bytes": len(body),
        "response_sha256": sha(body) if body else None,
        "response_headers": headers,
        "content_type": headers.get("Content-Type"),
        "source_response_sha256": SOURCE_SHA256,
    }
    write_once(raw_path, body)
    write_once(result_path, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("http_status", "transport_error", "response_bytes", "response_sha256", "content_type", "request_count", "retry_count")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
