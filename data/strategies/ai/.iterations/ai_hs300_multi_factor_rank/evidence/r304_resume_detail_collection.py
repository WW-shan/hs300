from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

E = Path(__file__).resolve().parent
ARCHIVE = E / "r304_official_archive"
PAGES = ARCHIVE / "pages"
DETAILS = ARCHIVE / "details"
ATTACHMENTS = ARCHIVE / "attachments"
RECEIPTS = ARCHIVE / "request_receipts"
SUMMARY = E / "r304_official_detail_collection.json"
START, END = "2018-01-02", "2026-10-27"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def save_json(path: Path, value: dict) -> str:
    return save(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def encoded_url(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="=&;%:@!$'()*+,-._~")
    return urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def fetch(url: str) -> tuple[int | None, bytes, dict, str | None]:
    ascii_url = encoded_url(url)
    req = urllib.request.Request(ascii_url, headers={
        "Accept": "application/json, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://www.csindex.com.cn",
        "Referer": "https://www.csindex.com.cn/",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            return response.status, response.read(), dict(response.headers.items()), None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items()), None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, b"", {}, f"{type(exc).__name__}: {exc}"


def selected_notices() -> list[dict]:
    pages = sorted(PAGES.glob("page-[0-9][0-9][0-9].json"))
    if len(pages) != 43:
        raise RuntimeError(f"expected 43 already-frozen pages, got {len(pages)}")
    by_id: dict[str, dict] = {}
    for page in pages:
        wrapper = json.loads(page.read_text(encoding="utf-8"))
        for row in (wrapper.get("response_json") or {}).get("data") or []:
            notice_id = str(row.get("id"))
            if notice_id in by_id:
                fields = ("publishDate", "title", "theme", "noticeType", "fileName", "fileUrl")
                if any(by_id[notice_id].get(field) != row.get(field) for field in fields):
                    raise RuntimeError(f"conflicting duplicate listing ID {notice_id}")
            else:
                by_id[notice_id] = row
    selected = []
    for row in by_id.values():
        day = str(row.get("publishDate") or "")[:10]
        title = str(row.get("title") or "").lower().replace(" ", "")
        if START <= day <= END and any(term in title for term in ("沪深300", "000300", "csi300")):
            selected.append(row)
    return sorted(selected, key=lambda row: (str(row.get("publishDate") or ""), int(row["id"])))


def valid_saved_detail(notice_id: str) -> dict | None:
    raw_path = DETAILS / f"notice-{notice_id}.raw.json"
    json_path = DETAILS / f"notice-{notice_id}.json"
    sidecar = raw_path.with_name(raw_path.name + ".sha256")
    if not raw_path.is_file() or not json_path.is_file() or not sidecar.is_file():
        return None
    expected = sidecar.read_text(encoding="ascii").split()[0]
    raw = raw_path.read_bytes()
    if sha(raw) != expected:
        raise RuntimeError(f"saved detail raw hash mismatch for notice {notice_id}")
    return json.loads(json_path.read_text(encoding="utf-8"))


def write_receipt(kind: str, key: str, receipt: dict) -> None:
    save_json(RECEIPTS / f"{kind}-{key}.json", receipt)


def main() -> None:
    if SUMMARY.exists() or SUMMARY.with_name(SUMMARY.name + ".sha256").exists():
        raise RuntimeError("detail collection summary exists; refusing overwrite")
    amendment_path = E / "r304_preregistration_amendment_A3.json"
    prereg_path = E / "r304_preregistration.json"
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    if amendment.get("parent_preregistration_sha256") != sha(prereg_path.read_bytes()):
        raise RuntimeError("A3 amendment parent hash mismatch")

    selected = selected_notices()
    details_meta, attachments_meta = [], []
    for index, listing in enumerate(selected, 1):
        notice_id = str(listing["id"])
        saved = valid_saved_detail(notice_id)
        if saved is None:
            url = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id=" + notice_id
            status, raw, headers, error = fetch(url)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            wrapper = {"round": "R304", "notice_id": notice_id, "listing": listing,
                       "original_url": url, "request_url": encoded_url(url), "http_status": status,
                       "transport_error": error, "response_sha256": sha(raw) if raw else None,
                       "response_bytes": len(raw), "response_headers": headers,
                       "response_json": payload,
                       "response_text": raw.decode("utf-8", "replace") if raw and payload is None else None}
            if raw:
                save(DETAILS / f"notice-{notice_id}.raw.json", raw)
            save_json(DETAILS / f"notice-{notice_id}.json", wrapper)
            write_receipt("detail", notice_id, {key: wrapper[key] for key in ("round", "notice_id", "original_url", "request_url", "http_status", "transport_error", "response_sha256", "response_bytes")})
            saved = wrapper
        else:
            wrapper = saved
        response = wrapper.get("response_json") or {}
        payload = response.get("data") or {} if isinstance(response, dict) else {}
        details_meta.append({"notice_id": notice_id, "listing": listing,
                             "http_status": wrapper.get("http_status"),
                             "transport_error": wrapper.get("transport_error"),
                             "response_sha256": wrapper.get("response_sha256"),
                             "payload_code": response.get("code") if isinstance(response, dict) else None})

        for item in payload.get("enclosureList") or []:
            file_url = str(item.get("fileUrl") or "")
            receipt_key = f"{notice_id}-{item.get('id')}"
            filename = Path(urlsplit(file_url).path).name
            local_path = ATTACHMENTS / notice_id / filename
            if local_path.is_file():
                sidecar = local_path.with_name(local_path.name + ".sha256")
                expected = sidecar.read_text(encoding="ascii").split()[0] if sidecar.is_file() else None
                actual = sha(local_path.read_bytes())
                if expected and expected != actual:
                    raise RuntimeError(f"saved attachment hash mismatch: {local_path}")
                attachments_meta.append({"notice_id": notice_id, "attachment_id": item.get("id"),
                    "file_name": item.get("fileName"), "file_url": file_url,
                    "local_relative_path": local_path.relative_to(ARCHIVE).as_posix(),
                    "response_sha256": actual, "response_bytes": local_path.stat().st_size,
                    "source": "previously_saved_no_repeat_request"})
                continue
            if not file_url.startswith("https://oss-ch.csindex.com.cn/"):
                attachments_meta.append({"notice_id": notice_id, "attachment_id": item.get("id"),
                    "file_name": item.get("fileName"), "file_url": file_url,
                    "skipped": "outside_official_csi_oss_host"})
                continue
            status, raw, headers, error = fetch(file_url)
            if raw:
                save(local_path, raw)
            receipt = {"round": "R304", "notice_id": notice_id, "attachment_id": item.get("id"),
                "original_url": file_url, "request_url": encoded_url(file_url), "http_status": status,
                "transport_error": error, "response_sha256": sha(raw) if raw else None,
                "response_bytes": len(raw), "response_headers": headers}
            write_receipt("attachment", receipt_key, receipt)
            attachments_meta.append({"notice_id": notice_id, "attachment_id": item.get("id"),
                "file_name": item.get("fileName"), "file_url": file_url, "http_status": status,
                "transport_error": error, "response_sha256": sha(raw) if raw else None,
                "response_bytes": len(raw), "local_relative_path": local_path.relative_to(ARCHIVE).as_posix() if raw else None})
        if index % 10 == 0:
            print(json.dumps({"selected_done": index, "selected_total": len(selected),
                              "details": len(details_meta), "attachments": len(attachments_meta)}, ensure_ascii=False))
            time.sleep(0.08)

    summary = {"round": "R304", "classification": "resumed_official_detail_and_attachment_collection",
        "captured_at_utc": datetime.now(UTC).isoformat(), "preregistration_sha256": sha(prereg_path.read_bytes()),
        "amendment_A2_sha256": sha((E / "r304_preregistration_amendment_A2.json").read_bytes()),
        "amendment_A3_sha256": sha(amendment_path.read_bytes()),
        "selected_title_candidate_count": len(selected), "selected_listing_rows": selected,
        "details": details_meta, "attachments": attachments_meta,
        "detail_http_success_count": sum(row["http_status"] == 200 and row["payload_code"] == "200" for row in details_meta),
        "attachment_http_200_count": sum(row.get("http_status") == 200 and not row.get("transport_error") for row in attachments_meta),
        "attachment_total": len(attachments_meta), "no_alpha_or_backtest": True}
    save_json(SUMMARY, summary)
    print(json.dumps({"title_candidates": len(selected), "details": len(details_meta),
        "detail_http_success": summary["detail_http_success_count"], "attachments": len(attachments_meta),
        "attachment_http_200": summary["attachment_http_200_count"],
        "summary_sha256": sha(SUMMARY.read_bytes())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
