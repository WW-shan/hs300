from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A9.json"
PRIOR_PANEL = EVIDENCE / "r304_A7_panel_final_readback.json"
PANEL_API = "http://127.0.0.1:3018/api/backtest/candidates"
NOTICE_ID = "12590"
NOTICE_URL = (
    "https://www.csindex.com.cn/csindex-home/announcement/"
    f"queryAnnouncementById?id={NOTICE_ID}"
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def get(url: str, *, headers: dict[str, str] | None = None) -> tuple[int | None, bytes, dict[str, str], str | None]:
    request = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), dict(response.headers.items()), None
    except urllib.error.HTTPError as error:
        return error.code, error.read(), dict(error.headers.items()), None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, b"", {}, f"{type(error).__name__}: {error}"


def parse(raw: bytes) -> dict | None:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else None
        return value if isinstance(value, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def main() -> None:
    amendment_raw = AMENDMENT.read_bytes()
    amendment = json.loads(amendment_raw)
    prior = json.loads(PRIOR_PANEL.read_text(encoding="utf-8"))
    fixed = amendment.get("fixed_target", {})
    if amendment.get("amendment_id") != "R304-A9" or fixed.get("official_api_url") != NOTICE_URL:
        raise RuntimeError("A9 amendment or fixed URL mismatch")
    if sha(EVIDENCE.joinpath(fixed["listing_evidence"]).read_bytes()) != fixed.get("listing_evidence_sha256"):
        raise RuntimeError("A9 fixed official listing evidence hash mismatch")
    if prior.get("r304_match_count") != 1 or prior.get("r304_candidate_status") != "pending":
        raise RuntimeError("A7 panel proof does not establish one pending R304 candidate")

    outputs = (
        EVIDENCE / "r304_A9_panel_readback.raw",
        EVIDENCE / "r304_A9_panel_readback.json",
        EVIDENCE / "r304_A9_dispatch.json",
        EVIDENCE / "r304_A9_detail_response.raw",
        EVIDENCE / "r304_A9_detail_response.json",
    )
    for path in outputs:
        if path.exists() or path.with_name(path.name + ".sha256").exists():
            raise RuntimeError(f"A9 evidence exists; refusing repeat request or overwrite: {path.name}")

    # The panel read is a separate read-only preflight; no panel mutation is made.
    panel_status, panel_raw, _panel_headers, panel_error = get(PANEL_API, headers={"Accept": "application/json"})
    panel_payload = parse(panel_raw)
    items = panel_payload.get("items", []) if panel_payload else []
    candidate_id = amendment["panel"]["candidate_id"]
    matches = [item for item in items if str(item.get("id")) == candidate_id]
    pending_unique = panel_status == 200 and len(matches) == 1 and matches[0].get("status") == "pending"
    write_once(outputs[0], panel_raw)
    panel_proof = {
        "round": "R304-A9",
        "classification": "read_only_panel_preflight_before_official_request",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": panel_status,
        "transport_error": panel_error,
        "response_sha256": sha(panel_raw) if panel_raw else None,
        "candidate_count": len(items),
        "r304_match_count": len(matches),
        "r304_candidate_status": matches[0].get("status") if len(matches) == 1 else None,
        "pending_unique": pending_unique,
        "panel_post_count": 0,
        "response": panel_payload,
    }
    write_once(outputs[1], (json.dumps(panel_proof, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    if not pending_unique:
        raise RuntimeError("A9 panel preflight failed; official request was not dispatched")

    intent = {
        "round": "R304-A9",
        "classification": "single_official_detail_get_dispatch_intent",
        "dispatch_intent_at_utc": datetime.now(UTC).isoformat(),
        "notice_id": NOTICE_ID,
        "url": NOTICE_URL,
        "amendment_sha256": sha(amendment_raw),
        "panel_readback_sha256": sha(outputs[1].read_bytes()),
        "request_count": 0,
        "retry_policy": "never retry after dispatch",
    }
    write_once(outputs[2], (json.dumps(intent, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    status, body, response_headers, error = get(
        NOTICE_URL,
        headers={
            "Accept": "application/json, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://www.csindex.com.cn",
            "Referer": "https://www.csindex.com.cn/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    payload = parse(body)
    detail = payload.get("data", {}) if payload else {}
    article = str(detail.get("content", "")) if isinstance(detail, dict) else ""
    member_code_tokens = sorted(set(re.findall(r"(?<!\d)\d{6}(?!\d)", article)))
    result = {
        "round": "R304-A9",
        "classification": "official_detail_response_captured_once_no_retry",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "request_count": 1,
        "retry_count": 0,
        "url": NOTICE_URL,
        "http_status": status,
        "transport_error": error,
        "response_bytes": len(body),
        "response_sha256": sha(body) if body else None,
        "response_headers": response_headers,
        "parsed_response": payload,
        "content_classification": {
            "title": detail.get("title") if isinstance(detail, dict) else None,
            "publish_date": detail.get("publishDate") if isinstance(detail, dict) else None,
            "mentions_hs300": "沪深300" in article or "000300" in article,
            "member_code_tokens": member_code_tokens,
            "complete_member_names_present": "requires manual article review",
            "contains_attachment_reference": bool(detail.get("enclosureList")) if isinstance(detail, dict) else False,
            "article_content_bytes": len(article.encode("utf-8")),
            "seed_for_2018_01_02_supported": False,
            "interpretation": "Pending manual classification of this single retained official response; no follow-up request is permitted.",
        },
    }
    write_once(outputs[3], body)
    write_once(outputs[4], (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({key: result[key] for key in ("http_status", "transport_error", "response_bytes", "response_sha256", "request_count", "retry_count", "content_classification")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
