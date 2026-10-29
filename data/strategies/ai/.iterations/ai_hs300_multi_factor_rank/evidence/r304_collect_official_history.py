from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
E = Path(__file__).resolve().parent
BASE = "https://www.csindex.com.cn/csindex-home"
LIST_URL = BASE + "/announcement/queryAnnouncementByVo"
DETAIL_URL = BASE + "/announcement/queryAnnouncementById"
TOTAL, SIZE = 4216, 100
START, END = "2018-01-02", "2026-10-27"
ARCHIVE = E / "r304_official_archive"
INDEX = E / "r304_official_archive_index.json"
AUDIT = E / "r304_official_history_audit.json"
FAILURE = E / "r304_official_archive_failure.json"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def save(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = digest(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def save_json(path: Path, value: dict) -> str:
    return save(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def request(url: str, method: str, payload: dict | None = None) -> tuple[int | None, bytes, dict, str | None]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
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


def body_for(page: int) -> dict:
    return {"lang": "cn", "classlist": [], "indexlist": [],
            "page": {"desc": "", "key": "", "page": page, "rows": SIZE},
            "related_topics": [], "typelist": []}


def main() -> None:
    if ARCHIVE.exists() or INDEX.exists() or AUDIT.exists() or FAILURE.exists():
        raise RuntimeError("R304 archive output exists; refusing overwrite/retry")
    prereg_path = E / "r304_preregistration.json"
    amendment_path = E / "r304_preregistration_amendment.json"
    prereg_sha = digest(prereg_path.read_bytes())
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    if amendment.get("parent_preregistration_sha256") != prereg_sha:
        raise RuntimeError("amendment parent hash mismatch")
    frozen_path = E / "r304_official_news_page100.json"
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    first = frozen.get("response_json") or {}
    if frozen.get("http_status") != 200 or first.get("code") != "200" or int(first.get("total") or -1) != TOTAL or len(first.get("data") or []) != SIZE:
        raise RuntimeError("frozen official first page does not match the amended scope")

    ARCHIVE.mkdir()
    pages_dir = ARCHIVE / "pages"
    records: list[dict] = []
    page_meta: list[dict] = []

    def accept(page: int, payload: dict, raw: bytes, status: int, source: str) -> None:
        rows = payload.get("data") if isinstance(payload, dict) else None
        expected = min(SIZE, TOTAL - (page - 1) * SIZE)
        if status != 200 or payload.get("code") != "200" or payload.get("success") is not True:
            raise ValueError(f"page {page} unsuccessful")
        if int(payload.get("total") or -1) != TOTAL or not isinstance(rows, list) or len(rows) != expected:
            raise ValueError(f"page {page} total/row count mismatch")
        ids = [str(row.get("id")) for row in rows]
        if any(not value or value == "None" for value in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"page {page} has missing/duplicate IDs")
        records.extend(rows)
        page_meta.append({"page": page, "source": source, "status": status,
                          "sha256": digest(raw), "bytes": len(raw), "rows": len(rows),
                          "first_id": ids[0], "last_id": ids[-1],
                          "first_date": rows[0].get("publishDate"), "last_date": rows[-1].get("publishDate")})

    frozen_raw = frozen_path.read_bytes()
    accept(1, first, frozen_raw, 200, "frozen_page1_rows100")
    save_json(pages_dir / "page-001.json", {"url": LIST_URL, "request": body_for(1),
        "http_status": 200, "response_sha256": frozen.get("response_sha256"),
        "response_json": first, "source": "r304_official_news_page100.json"})

    page_count = math.ceil(TOTAL / SIZE)
    for page in range(2, page_count + 1):
        status, raw, _headers, error = request(LIST_URL, "POST", body_for(page))
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            accept(page, payload, raw, status or 0, "live_official_api")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            failure = {"round": "R304", "classification": "official_archive_pagination_failed",
                       "captured_at_utc": datetime.now(UTC).isoformat(), "failed_page": page,
                       "http_status": status, "transport_error": error, "error": str(exc),
                       "response_sha256": digest(raw) if raw else None, "response_bytes": len(raw),
                       "completed_page_count": len(page_meta), "partial_listing_count": len(records),
                       "completed_pages": page_meta, "retry_policy": "no retry without a new amendment"}
            if raw:
                save(pages_dir / f"page-{page:03d}-invalid.bin", raw)
            save_json(FAILURE, failure)
            print(json.dumps({k: failure[k] for k in ("failed_page", "completed_page_count", "partial_listing_count", "error")}, ensure_ascii=False))
            return
        save_json(pages_dir / f"page-{page:03d}.json", {"url": LIST_URL, "request": body_for(page),
            "http_status": status, "response_sha256": digest(raw), "response_json": payload})
        time.sleep(0.05)

    all_ids = [str(row["id"]) for row in records]
    if len(records) != TOTAL or len(set(all_ids)) != TOTAL or len(page_meta) != page_count:
        raise RuntimeError("archive scan is not exactly 4,216 unique records over 43 pages")

    def in_window(row: dict) -> bool:
        day = str(row.get("publishDate") or "")[:10]
        return START <= day <= END

    def is_hs300(row: dict) -> bool:
        title = str(row.get("title") or "").lower().replace(" ", "")
        return any(token in title for token in ("沪深300", "000300", "csi300"))

    window = [row for row in records if in_window(row)]
    adjustment = [row for row in window if row.get("theme") == "指数调样"]
    related = [row for row in window if is_hs300(row)]
    notice_ids = sorted({str(row["id"]) for row in related}, key=int)
    details, attachments = [], []
    details_dir, attachment_dir = ARCHIVE / "details", ARCHIVE / "attachments"

    for index, notice_id in enumerate(notice_ids, 1):
        url = DETAIL_URL + "?id=" + notice_id
        status, raw, headers, error = request(url, "GET")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        detail = {"notice_id": notice_id, "url": url, "http_status": status,
                  "transport_error": error, "response_sha256": digest(raw) if raw else None,
                  "response_bytes": len(raw), "content_type": headers.get("Content-Type"),
                  "response_json": payload,
                  "response_text": raw.decode("utf-8", "replace") if raw and payload is None else None}
        save_json(details_dir / f"notice-{notice_id}.json", detail)
        details.append({k: detail[k] for k in ("notice_id", "url", "http_status", "transport_error", "response_sha256", "response_bytes")})
        if isinstance(payload, dict) and payload.get("code") == "200":
            for attachment in (payload.get("data") or {}).get("enclosureList") or []:
                file_url = str(attachment.get("fileUrl") or "")
                if not file_url.startswith("https://oss-ch.csindex.com.cn/"):
                    attachments.append({"notice_id": notice_id, "file_url": file_url, "skipped": "outside_official_host"})
                    continue
                a_status, a_raw, a_headers, a_error = request(file_url, "GET")
                filename = Path(urlparse(file_url).path).name or f"attachment-{attachment.get('id')}"
                local = Path(notice_id) / filename
                if a_raw:
                    save(attachment_dir / local, a_raw)
                attachments.append({"notice_id": notice_id, "attachment_id": attachment.get("id"),
                    "file_name": attachment.get("fileName"), "file_url": file_url,
                    "http_status": a_status, "transport_error": a_error,
                    "content_type": a_headers.get("Content-Type"),
                    "response_sha256": digest(a_raw) if a_raw else None, "response_bytes": len(a_raw),
                    "local_relative_path": local.as_posix() if a_raw else None})
        if index % 10 == 0:
            print(json.dumps({"details_done": index, "details_total": len(notice_ids)}, ensure_ascii=False))
            time.sleep(0.08)

    listing_min = min(str(row.get("publishDate") or "")[:10] for row in records)
    listing_max = max(str(row.get("publishDate") or "")[:10] for row in records)
    index_value = {"round": "R304", "classification": "full_primary_source_notice_archive",
        "captured_at_utc": datetime.now(UTC).isoformat(), "expected_total": TOTAL,
        "page_count": page_count, "pages": page_meta, "listing_count": len(records),
        "unique_listing_id_count": len(set(all_ids)), "publication_date_min": listing_min,
        "publication_date_max": listing_max, "target_window": [START, END],
        "target_window_listing_count": len(window), "target_window_adjustment_theme_count": len(adjustment),
        "target_window_hs300_related_count": len(related), "target_window_hs300_notice_ids": notice_ids,
        "details": details, "attachments": attachments,
        "detail_http_success_count": sum(row["http_status"] == 200 and not row["transport_error"] for row in details),
        "attachment_http_success_count": sum(row["http_status"] == 200 and not row["transport_error"] for row in attachments),
        "listing_complete": len(records) == TOTAL and len(set(all_ids)) == TOTAL and len(page_meta) == page_count,
        "official_urls": {"listing": LIST_URL, "detail": DETAIL_URL},
        "preregistration_sha256": prereg_sha, "amendment_sha256": digest(amendment_path.read_bytes())}
    index_sha = save_json(INDEX, index_value)
    audit = {"round": "R304", "classification": "official_history_source_coverage_audit",
        "captured_at_utc": datetime.now(UTC).isoformat(), "archive_index_sha256": index_sha,
        "listing_complete": index_value["listing_complete"],
        "publication_date_coverage": [listing_min, listing_max], "target_window": [START, END],
        "target_window_listing_count": len(window), "target_window_adjustment_theme_count": len(adjustment),
        "target_window_hs300_related_count": len(related), "target_window_hs300_notice_ids": notice_ids,
        "detail_count": len(details), "detail_http_success_count": index_value["detail_http_success_count"],
        "attachment_count": len(attachments), "attachment_http_success_count": index_value["attachment_http_success_count"],
        "next_requirement": "Extract exact effective dates and changes from every official attachment and reconcile the seed membership; listing completeness alone is not PIT proof.",
        "no_alpha_or_backtest": True}
    save_json(AUDIT, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
