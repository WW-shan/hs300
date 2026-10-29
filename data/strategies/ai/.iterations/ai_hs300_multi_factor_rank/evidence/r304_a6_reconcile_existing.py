from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "r304_official_archive"
PREREG = ROOT / "r304_preregistration_amendment_A6.json"
PANEL_URL = "http://127.0.0.1:3018/api/backtest/candidates"
PANEL_ID = "1ed2c75285b74fc6a521cc73f75f7320"
PANEL_NAME = "R304 中证官方历史成分数据源可行性（无收益回测）"
EXPECTED_ROUTINE = {
    "2018-06": ("11518", 27, "2018-06-11"),
    "2018-12": ("11859", 24, "2018-12-17"),
    "2019-06": ("11379", 19, "2019-06-17"),
    "2019-12": ("11529", 16, "2019-12-16"),
    "2020-06": ("11429", 21, "2020-06-15"),
    "2020-12": ("11514", 26, "2020-12-14"),
    "2021-06": ("12470", 25, "2021-06-11"),
    "2021-12": ("13888", 28, "2021-12-10"),
    "2022-12": ("14497", 15, "2022-12-19"),
    "2023-06": ("14796", 9, "2023-06-12"),
    "2023-12": ("15044", 14, "2023-12-11"),
    "2024-06": ("15267", 12, "2024-06-17"),
    "2024-12": ("15471", 16, "2024-12-16"),
    "2025-06": ("15690", 7, "2025-06-16"),
    "2025-12": ("3006000", 11, "2025-12-15"),
    "2026-06": ("3006137", 19, "2026-06-15"),
}
EXCEPTIONS = ("15546", "1006022", "3006227")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    checksum = digest(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{checksum}  {path.name}\n")
    return checksum


def panel_get() -> dict:
    request = urllib.request.Request(PANEL_URL, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"http_status": None, "transport_error": f"{type(error).__name__}: {error}", "response_sha256": None}
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = None
    items = body.get("items", []) if isinstance(body, dict) else []
    matches = [item for item in items if str(item.get("id")) == PANEL_ID and item.get("name") == PANEL_NAME]
    return {
        "endpoint": PANEL_URL,
        "http_status": status,
        "response_sha256": digest(raw),
        "candidate_count": len(items),
        "r304_match_count": len(matches),
        "r304_status": matches[0].get("status") if len(matches) == 1 else None,
        "response": body,
    }


def expected_hash_from_sidecar(path: Path) -> str | None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        return None
    value = sidecar.read_text(encoding="utf-8").strip()
    if not value:
        return ""
    return value.split()[0]


def verify_file(path: Path, anomalies: list[dict]) -> str:
    actual = digest(path.read_bytes())
    expected = expected_hash_from_sidecar(path)
    if expected != actual:
        anomalies.append({"path": path.relative_to(ARCHIVE).as_posix(), "expected": expected, "actual": actual})
    return actual


def html_content(notice_id: str) -> tuple[dict, BeautifulSoup, str]:
    path = ARCHIVE / "details" / f"notice-{notice_id}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    response = record.get("response_json") or {}
    data = response.get("data") or {}
    html = data.get("content", "") if isinstance(data, dict) else ""
    soup = BeautifulSoup(html, "html.parser")
    return record, soup, " ".join(soup.stripped_strings)


def table_under_hs300_heading(soup: BeautifulSoup) -> list[list[str]]:
    heading = next((node for node in soup.find_all(string=re.compile(r"沪深\s*300\s*指数样本(?:股)?调整名单"))), None)
    if heading is None:
        return []
    table = heading.parent.find_next("table")
    if table is None:
        return []
    return [[cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])] for row in table.find_all("tr")]


def codes_by_side(rows: list[list[str]]) -> dict[str, list[str]]:
    result = {"removed": [], "added": []}
    if len(rows) < 3:
        return result
    for row in rows[2:]:
        if len(row) >= 4:
            if re.fullmatch(r"\d{6}", row[0]):
                result["removed"].append(row[0])
            if re.fullmatch(r"\d{6}", row[2]):
                result["added"].append(row[2])
    return result


def notice_effective_date(text: str) -> str | None:
    match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日[^。；]{0,40}(?:生效|实施|调整)", text)
    if not match:
        match = re.search(r"(?:自|于)(20\d{2})年(\d{1,2})月(\d{1,2})日[^。；]{0,40}(?:生效|实施|调整)", text)
    if match:
        year, month, day = (int(value) for value in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def routine_notice(notice_id: str, expected_count: int, expected_effective: str) -> dict:
    record, soup, text = html_content(notice_id)
    rows = table_under_hs300_heading(soup)
    sides = codes_by_side(rows)
    actual_count = max(len(sides["removed"]), len(sides["added"]))
    effective = notice_effective_date(text)
    return {
        "notice_id": notice_id,
        "http_status": record.get("http_status"),
        "listing_title": record.get("listing", {}).get("title"),
        "stated_effective_date": effective,
        "expected_effective_date": expected_effective,
        "declared_replacement_count": expected_count,
        "table_rows": len(rows),
        "removed_count": len(sides["removed"]),
        "added_count": len(sides["added"]),
        "removed_codes": sides["removed"],
        "added_codes": sides["added"],
        "count_match": len(sides["removed"]) == expected_count and len(sides["added"]) == expected_count,
        "date_match": effective == expected_effective,
        "record_sha256": digest((ARCHIVE / "details" / f"notice-{notice_id}.json").read_bytes()),
    }


def attachment_events() -> list[dict]:
    from openpyxl import load_workbook
    import fitz

    results = []
    for event, (notice_id, expected_count, expected_date) in EXPECTED_ROUTINE.items():
        year = int(event[:4])
        if year < 2022:
            continue
        directory = ARCHIVE / "attachments" / notice_id
        candidates = [path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in {".pdf", ".xlsx", ".xls"}]
        if not candidates:
            results.append({"event": event, "notice_id": notice_id, "missing_attachment": True})
            continue
        path = candidates[0]
        digest_value = digest(path.read_bytes())
        if path.suffix.lower() == ".xlsx":
            book = load_workbook(path, read_only=True, data_only=True)
            sides: dict[str, list[str]] = {"added": [], "removed": []}
            for sheet_name, key in (("调入", "added"), ("调出", "removed")):
                sheet = book[sheet_name]
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    if row and str(row[0]).strip() == "000300" and str(row[1]).strip() == "沪深300":
                        code = str(row[2]).strip()
                        if code.isdigit():
                            sides[key].append(code.zfill(6))
            book.close()
            source_kind = "xlsx_000300_rows"
        else:
            doc = fitz.open(path)
            full_text = "\n".join(page.get_text() for page in doc)
            doc.close()
            start = re.search(r"沪深\s*300\s*指数样本调整名单", full_text)
            tail = full_text[start.end():] if start else ""
            end = re.search(r"中证\s*(?:100|200|500|1000|A500|全指)\s*指数样本调整名单", tail)
            section = tail[:end.start()] if end else tail
            codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", section)
            # The official PDF presents paired removed/added columns row by row.
            sides = {"removed": codes[0::2], "added": codes[1::2]}
            source_kind = "pdf_hs300_section"
        results.append({
            "event": event,
            "notice_id": notice_id,
            "attachment": path.relative_to(ARCHIVE).as_posix(),
            "attachment_sha256": digest_value,
            "source_kind": source_kind,
            "removed_codes": sides["removed"],
            "added_codes": sides["added"],
            "removed_count": len(sides["removed"]),
            "added_count": len(sides["added"]),
            "declared_replacement_count": expected_count,
            "expected_effective_date": expected_date,
            "count_match": len(sides["removed"]) == expected_count and len(sides["added"]) == expected_count,
        })
    return results


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError("A6 preregistration missing; refusing to run")
    prereg_raw = PREREG.read_bytes()
    prereg_sha = digest(prereg_raw)
    prereg_sidecar = PREREG.with_name(PREREG.name + ".sha256")
    if not prereg_sidecar.exists():
        with prereg_sidecar.open("x", encoding="ascii") as stream:
            stream.write(f"{prereg_sha}  {PREREG.name}\n")

    output = ROOT / "r304_A6_local_reconciliation_v3.json"
    panel_proof = ROOT / "r304_A6_panel_readback.json"
    for path in (output, output.with_name(output.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"A6 output already exists; refusing overwrite: {path.name}")

    if panel_proof.exists():
        panel_evidence = json.loads(panel_proof.read_text(encoding="utf-8"))
        panel_sha = digest(panel_proof.read_bytes())
    else:
        panel = panel_get()
        panel_evidence = {"round": "R304-A6", "captured_at_utc": datetime.now(UTC).isoformat(), **panel}
        panel_sha = write_once(panel_proof, (json.dumps(panel_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    anomalies: list[dict] = []
    input_hashes: dict[str, str] = {}
    page_rows = []
    listing_ids = []
    for number in range(1, 44):
        path = ARCHIVE / "pages" / f"page-{number:03d}.json"
        if not path.exists():
            page_rows.append({"page": number, "missing": True})
            continue
        input_hashes[path.relative_to(ARCHIVE).as_posix()] = verify_file(path, anomalies)
        document = json.loads(path.read_text(encoding="utf-8"))
        response = document.get("response_json", {})
        data = response.get("data", [])
        listing_ids.extend(str(item.get("id")) for item in data)
        page_rows.append({
            "page": number,
            "http_status": document.get("http_status"),
            "response_total": response.get("total"),
            "page_size": response.get("pageSize"),
            "record_count": len(data),
            "first_date": data[0].get("publishDate") if data else None,
            "last_date": data[-1].get("publishDate") if data else None,
        })
    listing_counts = Counter(listing_ids)

    for path in sorted((ARCHIVE / "details").glob("notice-*.json")):
        if path.name.endswith(".raw.json"):
            continue
        input_hashes[path.relative_to(ARCHIVE).as_posix()] = verify_file(path, anomalies)
    for path in sorted((ARCHIVE / "details").glob("notice-*.raw.json")):
        input_hashes[path.relative_to(ARCHIVE).as_posix()] = verify_file(path, anomalies)
    for path in sorted((ARCHIVE / "attachments").rglob("*")):
        if path.is_file() and path.suffix.lower() in {".pdf", ".xlsx", ".xls"}:
            input_hashes[path.relative_to(ARCHIVE).as_posix()] = verify_file(path, anomalies)

    routine_results = []
    for event, (notice_id, count, effective) in EXPECTED_ROUTINE.items():
        if event[:4] < "2022":
            routine_results.append({"event": event, **routine_notice(notice_id, count, effective)})
    attachment_results = attachment_events()

    exception_results = []
    for notice_id in EXCEPTIONS:
        path = ARCHIVE / "details" / f"notice-{notice_id}.json"
        record, soup, text = html_content(notice_id)
        exception_results.append({
            "notice_id": notice_id,
            "http_status": record.get("http_status"),
            "listing_title": record.get("listing", {}).get("title"),
            "publish_date": record.get("listing", {}).get("publishDate"),
            "text_excerpt": text[:1600],
            "date_terms": re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日[^。；]{0,45}", text),
            "record_sha256": digest(path.read_bytes()),
        })

    # The archive itself must remain a local evidence set; hashes are calculated before this report is written.
    result = {
        "round": "R304-A6",
        "classification": "local_evidence_reconciliation_no_new_official_requests",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256": prereg_sha,
        "panel_readback_sha256": panel_sha,
        "panel_candidate_id": PANEL_ID,
        "panel_match_count": panel_evidence.get("r304_match_count"),
        "panel_status": panel_evidence.get("r304_status"),
        "listing_archive": {
            "expected_pages": 43,
            "present_pages": sum(1 for item in page_rows if not item.get("missing")),
            "pages": page_rows,
            "record_count": len(listing_ids),
            "unique_listing_ids": len(listing_counts),
            "duplicate_listing_ids": {key: count for key, count in listing_counts.items() if count > 1},
            "date_min": min((item["first_date"] for item in page_rows if item.get("first_date")), default=None),
            "date_max": max((item["first_date"] for item in page_rows if item.get("first_date")), default=None),
            "pre_window_routine_candidates": [
                {"id": str(item.get("id")), "publishDate": item.get("publishDate"), "title": item.get("title")}
                for number in range(1, 44)
                for item in json.loads((ARCHIVE / "pages" / f"page-{number:03d}.json").read_text(encoding="utf-8")).get("response_json", {}).get("data", [])
                if str(item.get("publishDate", "")) < "2018-01-02" and re.search(r"(沪深\s*300|000300)", str(item.get("title", ""))) and "定期" in str(item.get("title", ""))
            ],
        },
        "routine_2018_2021": routine_results,
        "routine_attachment_audit": attachment_results,
        "exceptional_events": exception_results,
        "sha256_anomalies": anomalies,
        "input_file_count": len(input_hashes),
        "input_hashes": input_hashes,
        "limitations": [
            "No 2018-01-02 official seed membership established.",
            "Pre-window official routine-adjustment notice candidates are identified from listing titles only; their details were not requested or inspected.",
            "An announcement-triggered exceptional change's exact delisting-effective date is not inferred from its publication date.",
            "The PDF code counts are mechanical text extraction and require table-structure review before being treated as an independently validated machine transcription.",
        ],
        "no_alpha_or_backtest": True,
        "no_official_network_requests": True,
    }
    output_sha = write_once(output, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({
        "round": result["round"],
        "panel_http_status": panel_evidence.get("http_status"),
        "panel_candidate_count": panel_evidence.get("candidate_count"),
        "panel_match_count": panel_evidence.get("r304_match_count"),
        "panel_status": panel_evidence.get("r304_status"),
        "listing_records": len(listing_ids),
        "unique_listing_ids": len(listing_counts),
        "routine_table_counts": [{"event": x["event"], "removed": x.get("removed_count"), "added": x.get("added_count"), "effective": x.get("stated_effective_date"), "count_match": x.get("count_match")} for x in routine_results],
        "attachment_counts": [{"event": x["event"], "removed": x.get("removed_count"), "added": x.get("added_count"), "count_match": x.get("count_match")} for x in attachment_results],
        "sha256_anomalies": anomalies,
        "input_file_count": len(input_hashes),
        "result_sha256": output_sha,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
