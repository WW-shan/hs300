from __future__ import annotations

import hashlib
import json
import platform
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
BASE = "https://www.csindex.com.cn/csindex-home"
FAILURE_OUTPUT = EVIDENCE / "r304_official_query_failure.json"
SUCCESS_OUTPUTS = (
    EVIDENCE / "r304_official_news_page1.json",
    EVIDENCE / "r304_official_news_page100.json",
    EVIDENCE / "r304_official_query_audit.json",
    EVIDENCE / "r304_exploratory_homepage.html",
    EVIDENCE / "r304_official_frontend_bundle.js",
    EVIDENCE / "r304_preregistration.json.sha256",
)
REQUESTS = (
    {
        "label": "page1",
        "path": "/announcement/queryAnnouncementByVo",
        "body": {
            "lang": "cn",
            "classlist": [],
            "indexlist": [],
            "page": {"desc": "", "key": "", "page": 1, "rows": 5},
            "related_topics": [],
            "typelist": [],
        },
    },
    {
        "label": "page100",
        "path": "/announcement/queryAnnouncementByVo",
        "body": {
            "lang": "cn",
            "classlist": [],
            "indexlist": [],
            "page": {"desc": "", "key": "", "page": 1, "rows": 100},
            "related_topics": [],
            "typelist": [],
        },
    },
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> str:
    digest = sha(raw)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def main() -> None:
    for output in (*SUCCESS_OUTPUTS, FAILURE_OUTPUT):
        if output.exists() or output.with_name(output.name + ".sha256").exists():
            raise RuntimeError(f"R304 query evidence exists; refusing overwrite: {output.name}")
    prereg = EVIDENCE / "r304_preregistration.json"
    js_path = EVIDENCE / "r304_official_frontend_bundle.js"
    home_path = EVIDENCE / "r304_csindex_home.html"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(
                "https://www.csindex.com.cn/static/js/app.d0fcb399.js",
                headers={"User-Agent": "Mozilla/5.0"},
            ),
            timeout=30,
        ) as response:
            app_js = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        failure = {
            "round": "R304",
            "classification": "official_source_query_failed_before_primary_endpoint_dispatch",
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "preregistration_sha256": sha(prereg.read_bytes()),
            "execution_script_sha256": sha(Path(__file__).read_bytes()),
            "failed_request_url": "https://www.csindex.com.cn/static/js/app.d0fcb399.js",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "primary_api_requests_dispatched": False,
            "retry_policy": "no blind retry; any new method must be preregistered as an amendment",
        }
        write_once(FAILURE_OUTPUT, (json.dumps(failure, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return
    js_path.write_bytes(app_js)
    if not home_path.is_file():
        raise FileNotFoundError("exploratory homepage response is absent")
    exploratory_home = home_path.read_bytes()

    prereg_data = json.loads(prereg.read_text(encoding="utf-8"))
    amendment = prereg_data.get("execution_addendum", {})
    if amendment.get("classification") != "prospective amendment after panel registration but before the fixed official API query; exploratory endpoint discovery occurred before this amendment":
        raise RuntimeError("R304 exploratory deviation is not documented")

    responses = []
    encoded_outputs = []
    for request in REQUESTS:
        raw_request = json.dumps(request["body"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            BASE + request["path"],
            data=raw_request,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://www.csindex.com.cn",
                "Referer": "https://www.csindex.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                status = response.status
                raw = response.read()
                headers = {key.lower(): value for key, value in response.headers.items()}
                error = None
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            headers = {key.lower(): value for key, value in exc.headers.items()}
            error = None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = None
            raw = b""
            headers = {}
            error = f"{type(exc).__name__}: {exc}"
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        record = {
            "label": request["label"],
            "method": "POST",
            "url": BASE + request["path"],
            "request_body": request["body"],
            "request_sha256": sha(raw_request),
            "http_status": status,
            "transport_error": error,
            "response_headers": {key: headers[key] for key in ("content-type", "date", "content-length") if key in headers},
            "response_sha256": sha(raw) if raw else None,
            "response_bytes": len(raw),
            "response_json": payload,
            "response_text": raw.decode("utf-8", "replace") if payload is None else None,
        }
        responses.append(record)
        encoded_outputs.append((request["label"], (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")))

    def scan(value, terms, found):
        if isinstance(value, dict):
            for child in value.values():
                scan(child, terms, found)
        elif isinstance(value, list):
            for child in value:
                scan(child, terms, found)
        elif isinstance(value, str):
            lowered = value.lower()
            if any(term.lower() in lowered for term in terms):
                found.append(value)

    term_matches = {}
    for response in responses:
        found = []
        scan(response.get("response_json"), ("000300", "沪深300", "沪深 300", "CSI 300"), found)
        term_matches[response["label"]] = found[:200]

    audit = {
        "round": "R304",
        "classification": "fixed_official_endpoint_query_after_documented_exploratory_deviation",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "preregistration_sha256_after_addendum": sha(prereg.read_bytes()),
        "execution_script_sha256": sha(Path(__file__).read_bytes()),
        "homepage_html_exploratory_sha256": sha(exploratory_home),
        "app_js_sha256": sha(app_js),
        "app_js_url": "https://www.csindex.com.cn/static/js/app.d0fcb399.js",
        "frontend_source_evidence": {
            "about_query_endpoint": "/csindex-home/announcement/queryAnnouncementByVo",
            "frontend_method": "POST",
            "frontend_request_shape": REQUESTS[0]["body"],
            "query_discovered_from_official_frontend_bundle": True,
        },
        "fixed_query_records": [{key: row[key] for key in ("label", "url", "request_sha256", "http_status", "transport_error", "response_sha256", "response_bytes")} for row in responses],
        "hs300_term_matches": term_matches,
        "all_queries_http_200": all(row["http_status"] == 200 for row in responses),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "deviation_note": amendment.get("deviation"),
        "no_alpha_or_backtest": True,
    }
    for label, raw in encoded_outputs:
        output = EVIDENCE / f"r304_official_news_{label}.json"
        write_once(output, raw)
    write_once(SUCCESS_OUTPUTS[2], (json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    write_once(SUCCESS_OUTPUTS[3], exploratory_home)
    write_once(SUCCESS_OUTPUTS[4], app_js)
    prereg_hash = sha(prereg.read_bytes())
    write_once(SUCCESS_OUTPUTS[5], (f"{prereg_hash}  r304_preregistration.json\n").encode("ascii"))
    print(json.dumps({
        "queries": audit["fixed_query_records"],
        "hs300_term_match_counts": {key: len(value) for key, value in term_matches.items()},
        "all_queries_http_200": audit["all_queries_http_200"],
        "app_js_sha256": audit["app_js_sha256"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
