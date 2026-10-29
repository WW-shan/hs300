from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

E = Path(__file__).resolve().parent
BASE = "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id="
ARCHIVE = E / "r304_official_archive"
DETAILS = ARCHIVE / "details"
ATTACHMENTS = ARCHIVE / "attachments"
SUMMARY = E / "r304_official_detail_collection.json"
FAILURE = E / "r304_official_detail_collection_failure.json"
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


def fetch(url: str) -> tuple[int | None, bytes, dict, str | None]:
    req = urllib.request.Request(url, headers={
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


def listing_rows() -> tuple[list[dict], list[dict]]:
    pages = sorted((ARCHIVE / "pages").glob("page-[0-9][0-9][0-9].json"))
    if len(pages) != 43:
        raise RuntimeError(f"expected 43 frozen official pages, got {len(pages)}")
    all_rows = []
    for page in pages:
        wrapper = json.loads(page.read_text(encoding="utf-8"))
        payload = wrapper.get("response_json") or {}
        rows = payload.get("data") or []
        if wrapper.get("http_status") not in (None, 200) or payload.get("code") != "200":
            raise RuntimeError(f"invalid frozen listing page: {page.name}")
        all_rows.extend(rows)
    grouped: dict[str, list[dict]] = {}
    for row in all_rows:
        grouped.setdefault(str(row.get("id")), []).append(row)
    duplicate_audit = []
    for notice_id, copies in grouped.items():
        if len(copies) > 1:
            fields = ("publishDate", "title", "theme", "noticeType", "fileName", "fileUrl")
            projections = [{key: row.get(key) for key in fields} for row in copies]
            if any(item != projections[0] for item in projections[1:]):
                raise RuntimeError(f"conflicting repeated official notice ID: {notice_id}")
            duplicate_audit.append({"id": notice_id, "copies": len(copies), "identical_fields": projections[0]})
    unique = [copies[0] for copies in grouped.values()]
    if len(all_rows) != 4216 or len(unique) != 4206 or len(duplicate_audit) != 10:
        raise RuntimeError(f"unexpected frozen listing inventory: rows={len(all_rows)}, unique={len(unique)}, repeated={len(duplicate_audit)}")
    return unique, duplicate_audit


def main() -> None:
    if SUMMARY.exists() or SUMMARY.with_name(SUMMARY.name + ".sha256").exists() or FAILURE.exists():
        raise RuntimeError("R304 detail collector already has output; refusing overwrite/retry")
    unique, duplicate_audit = listing_rows()
    selected = []
    for row in unique:
        published = str(row.get("publishDate") or "")[:10]
        if not START <= published <= END:
            continue
        title = str(row.get("title") or "").lower().replace(" ", "")
        if any(term in title for term in ("沪深300", "000300", "csi300")):
            selected.append(row)
    selected.sort(key=lambda row: (str(row.get("publishDate") or ""), int(row["id"])))

    details = []
    attachments = []
    for index, row in enumerate(selected, start=1):
        notice_id = str(row["id"])
        url = BASE + notice_id
        status, raw, headers, error = fetch(url)
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        detail = {
            "round": "R304",
            "listing": row,
            "url": url,
            "http_status": status,
            "transport_error": error,
            "response_sha256": sha(raw) if raw else None,
            "response_bytes": len(raw),
            "content_type": headers.get("Content-Type"),
            "response_json": payload,
            "response_text": raw.decode("utf-8", "replace") if raw and payload is None else None,
        }
        if raw:
            save(DETAILS / f"notice-{notice_id}.raw.json", raw)
        save_json(DETAILS / f"notice-{notice_id}.json", detail)
        details.append({key: detail[key] for key in ("listing", "url", "http_status", "transport_error", "response_sha256", "response_bytes")})

        if isinstance(payload, dict) and payload.get("code") == "200":
            body = payload.get("data") or {}
            for item in body.get("enclosureList") or []:
                file_url = str(item.get("fileUrl") or "")
                receipt = {"notice_id": notice_id, "attachment_id": item.get("id"),
                           "file_name": item.get("fileName"), "file_url": file_url}
                if not file_url.startswith("https://oss-ch.csindex.com.cn/"):
                    receipt["skipped"] = "not_official_csi_oss_host"
                    attachments.append(receipt)
                    continue
                a_status, a_raw, a_headers, a_error = fetch(file_url)
                filename = Path(urlparse(file_url).path).name or f"attachment-{item.get('id')}"
                relative = Path(notice_id) / filename
                if a_raw:
                    save(ATTACHMENTS / relative, a_raw)
                receipt.update({
                    "http_status": a_status,
                    "transport_error": a_error,
                    "content_type": a_headers.get("Content-Type"),
                    "response_sha256": sha(a_raw) if a_raw else None,
                    "response_bytes": len(a_raw),
                    "local_relative_path": relative.as_posix() if a_raw else None,
                })
                attachments.append(receipt)
        if index % 10 == 0:
            print(json.dumps({"details_done": index, "details_total": len(selected)}, ensure_ascii=False))
            time.sleep(0.08)

    summary = {
        "round": "R304",
        "classification": "deduplicated_official_hs300_title_candidate_details_and_attachments",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "listing_rows": 4216,
        "unique_notice_ids": len(unique),
        "identical_cross_page_duplicates": duplicate_audit,
        "target_window": [START, END],
        "selected_title_candidate_count": len(selected),
        "selected_listing_rows": selected,
        "details": details,
        "attachments": attachments,
        "detail_http_200_count": sum(row["http_status"] == 200 and not row["transport_error"] for row in details),
        "attachment_http_200_count": sum(row.get("http_status") == 200 and not row.get("transport_error") for row in attachments),
        "official_source_only": True,
        "no_alpha_or_backtest": True,
    }
    save_json(SUMMARY, summary)
    print(json.dumps({
        "rows": summary["listing_rows"],
        "unique_ids": summary["unique_notice_ids"],
        "repeated_identical_ids": len(duplicate_audit),
        "selected_title_candidates": len(selected),
        "details": len(details),
        "detail_http_200": summary["detail_http_200_count"],
        "attachments": len(attachments),
        "attachment_http_200": summary["attachment_http_200_count"],
        "summary_sha256": sha(SUMMARY.read_bytes()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
