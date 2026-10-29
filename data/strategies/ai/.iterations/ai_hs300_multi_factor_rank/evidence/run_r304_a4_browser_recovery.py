from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
AMENDMENT = EVIDENCE / "r304_preregistration_amendment_A4.json"
CORRECTION = EVIDENCE / "r304_preregistration_amendment_A4_C1.json"
PREREG = EVIDENCE / "r304_preregistration.json"
ARCHIVE = EVIDENCE / "r304_official_archive"
PANEL_BASELINE = EVIDENCE / "r304_panel_A4_pre_readback.json"
PANEL_FINAL = EVIDENCE / "r304_panel_A4_final_readback.json"
OUTPUT = EVIDENCE / "r304_A4_browser_results.json"
API = "http://127.0.0.1:3018/api/backtest/candidates"
ORIGIN = "https://www.csindex.com.cn"
NAME = "R304 中证官方历史成分数据源可行性（无收益回测）"
IDS = (
    "14100", "14118", "14144", "14217", "14223", "14225",
    "14460", "14497", "14503", "14535", "14796", "14806",
    "15043", "15044", "15252", "15266", "15267", "15440",
    "15470", "15471", "15503", "15504", "15516", "15546",
    "15690", "15693", "1006022", "3006000", "3006002",
    "3006137", "3006139", "3006227",
)
STATUS_FILE = EVIDENCE / ".r304_a4_browser_state.json"
SCREENSHOTS = EVIDENCE / "r304_A4_browser_screenshots"
SNAPSHOTS = EVIDENCE / "r304_A4_browser_snapshots"
PLAYWRIGHT_CLI_PACKAGE = Path(os.environ["LOCALAPPDATA"]) / "npm-cache/_npx/31e32ef8478fbf80/node_modules/@playwright/cli/playwright-cli.js"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_once(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{sha(raw)}  {path.name}\n")


def read_panel() -> dict:
    request = urllib.request.Request(API, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
        return {"status": response.status, "raw": raw, "payload": json.loads(raw)}


def verify_panel(phase: str, output: Path) -> dict:
    panel = read_panel()
    items = panel["payload"].get("items", [])
    matches = [item for item in items if item.get("id") == "1ed2c75285b74fc6a521cc73f75f7320" and item.get("source_id") == "csi_hs300_historical_membership_source_audit"]
    protected = {
        "r303": [item for item in items if item.get("source_id") == "csi_hs300_nested_mining_universe_feasibility"],
    }
    result = {
        "phase": phase,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "http_status": panel["status"],
        "response_sha256": sha(panel["raw"]),
        "candidate_count": len(items),
        "r304_match_count": len(matches),
        "r304_candidate": matches[0] if len(matches) == 1 else None,
        "r304_pending": len(matches) == 1 and matches[0].get("status") == "pending",
        "protected_r303": protected["r303"],
        "all_pass": panel["status"] == 200 and len(matches) == 1 and matches[0].get("status") == "pending",
        "response": panel["payload"],
    }
    write_once(output, (json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    if not result["all_pass"]:
        raise RuntimeError(f"R304 panel {phase} check failed; no POST will be attempted")
    return result


def cli(*args: str, timeout: int = 90) -> tuple[int, str]:
    env = os.environ.copy()
    env["PLAYWRIGHT_CLI_SESSION"] = "r304-a4"
    process = subprocess.run(
        ["node", str(PLAYWRIGHT_CLI_PACKAGE), *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False, cwd=EVIDENCE, env=env,
    )
    return process.returncode, process.stdout + process.stderr


def browser_args() -> list[str]:
    return []


def persist_state(state: dict) -> None:
    STATUS_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_14225_addendum(state: dict) -> None:
    addendum = EVIDENCE / "r304_A4_14225_postload_addendum/manifest.json"
    if not addendum.is_file():
        raise RuntimeError("The already-dispatched 14225 navigation is incomplete and has no read-only addendum")
    proof = json.loads(addendum.read_text(encoding="utf-8"))
    if proof.get("notice_id") != "14225" or proof.get("detail_api_http_status") != 200:
        raise RuntimeError("14225 addendum does not prove the existing HTTP 200 detail")
    row = next((item for item in state["results"] if item.get("notice_id") == "14225"), None)
    if row is None:
        raise RuntimeError("14225 is missing from the dispatched-target ledger")
    row.update({
        "classification": "official_detail_api_200_article_recovered_postload",
        "article_text_present": True,
        "detail_api_http_status": 200,
        "article_title": proof["article_title"],
        "published_at": proof["published_at"],
        "stated_effective_at": proof["stated_effective_at"],
        "hs300_event": proof["hs300_event"],
        "postload_addendum": "r304_A4_14225_postload_addendum/manifest.json",
        "postload_evidence_sha256": proof["sha256"],
    })
    persist_state(state)


def main() -> None:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R304 A4 output exists; refusing to repeat browser navigations")
    amendment = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    prereg_sha = sha(PREREG.read_bytes())
    if amendment.get("parent_preregistration_sha256") != prereg_sha:
        raise RuntimeError("A4 parent preregistration hash mismatch")
    if tuple(amendment["fixed_targets"]["notice_ids"]) != IDS:
        raise RuntimeError("A4 target list differs from frozen script")
    if not PLAYWRIGHT_CLI_PACKAGE.is_file():
        raise RuntimeError(f"Playwright CLI package missing: {PLAYWRIGHT_CLI_PACKAGE}")

    if not PANEL_BASELINE.is_file():
        raise RuntimeError("A4 panel pre-readback proof is missing; no browser navigation will start")
    baseline = json.loads(PANEL_BASELINE.read_text(encoding="utf-8"))
    if baseline.get("http_status") != 200 or baseline.get("r304_candidate", {}).get("id") != "1ed2c75285b74fc6a521cc73f75f7320" or baseline.get("r304_candidate", {}).get("status") != "pending":
        raise RuntimeError("A4 saved panel pre-readback is not a valid pending proof")
    if STATUS_FILE.exists():
        prior = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        rows = prior.get("results", [])
        dispatched = [(row.get("index"), row.get("notice_id")) for row in rows]
        expected_prefix = list(enumerate(IDS[4:prior.get("target_index", 0)], 5))
        if prior.get("amendment") != "R304-A4" or dispatched != expected_prefix or prior.get("target_index") != len(prior.get("results", [])) + 4:
            raise RuntimeError("A4 state differs from the verified ordered at-most-once target prefix; refusing navigation")
        state = prior
        if not state.get("browser_start_attempted"):
            raise RuntimeError("A4 checkpoint does not record the browser session start; refusing navigation")
        apply_14225_addendum(state)
        incomplete = [row.get("notice_id") for row in state["results"] if row.get("navigation_status") == "dispatching_unknown" or "classification" not in row]
        if incomplete:
            raise RuntimeError(f"A4 has already-dispatched targets with incomplete evidence {incomplete}; recover read-only, never navigate them again")
    else:
        state = {
            "round": "R304", "amendment": "R304-A4",
            "started_at_utc": datetime.now(UTC).isoformat(),
            "target_index": 0, "results": [],
            "panel_baseline_sha256": sha(PANEL_BASELINE.read_bytes()),
            "browser_start_attempted": False,
        }
        persist_state(state)

    if not state.get("browser_start"):
        rc, output = cli("open", ORIGIN)
        state["browser_start_attempted"] = True
        state["browser_start"] = {"returncode": rc, "output": output, "at_utc": datetime.now(UTC).isoformat()}
        persist_state(state)
        if rc != 0:
            raise RuntimeError("Playwright browser failed to start; A4 target navigations were not dispatched")

    for index, notice_id in enumerate(IDS, 1):
        if index <= state["target_index"]:
            continue
        target = f"{ORIGIN}/#/about/newsDetail?id={notice_id}"
        started = datetime.now(UTC).isoformat()
        entry = {
            "index": index, "notice_id": notice_id, "target_url": target,
            "navigation_started_at_utc": started,
            "navigation_status": "dispatching_unknown", "retried": False,
        }
        state["results"].append(entry)
        state["target_index"] = index
        persist_state(state)
        try:
            rc, nav = cli("goto", target, timeout=20)
        except subprocess.TimeoutExpired as exc:
            rc, nav = 124, f"TimeoutExpired after dispatch; captured stdout={exc.stdout!r}; stderr={exc.stderr!r}; no retry"
        entry.update({
            "navigation_completed_at_utc": datetime.now(UTC).isoformat(),
            "navigation_returncode": rc, "navigation_output": nav,
            "navigation_status": "completed" if rc == 0 else "failed_no_retry",
        })
        persist_state(state)
        if rc != 0:
            entry["classification"] = "navigation_failed_no_retry"
            persist_state(state)
            continue

        archive = json.loads((EVIDENCE / "r304_official_detail_collection.json").read_text(encoding="utf-8"))
        expected = next((str(row.get("title", "")).strip() for row in archive.get("selected_listing_rows", []) if str(row.get("id")) == notice_id), "")
        for _ in range(12):
            _, loaded_text = cli("eval", "document.body.innerText", timeout=12)
            if expected and expected in loaded_text:
                break
            time.sleep(0.5)
        rc, snap = cli("snapshot", timeout=12)
        entry["snapshot_returncode"] = rc
        entry["snapshot"] = snap
        entry["page_title"] = None
        entry["final_url"] = None
        match_title = re.search(r"### Page URL\s*\n([^\n]+)", snap)
        if match_title:
            entry["final_url"] = match_title.group(1).strip()
        match_title = re.search(r"### Page Title\s*\n([^\n]+)", snap)
        if match_title:
            entry["page_title"] = match_title.group(1).strip()
        entry["observed_spa_route"] = notice_id in (entry["final_url"] or "")
        body_text = ""
        if rc == 0:
            eval_rc, body_text = cli("eval", "document.body.innerText", timeout=12)
            entry["body_text_eval_returncode"] = eval_rc
        entry["body_text"] = body_text
        entry["expected_official_title"] = expected or None
        entry["article_text_present"] = bool(expected) and expected in snap
        snapshot_path = SNAPSHOTS / f"notice-{notice_id}.txt"
        write_once(snapshot_path, snap.encode("utf-8"))
        entry["snapshot_path"] = snapshot_path.relative_to(EVIDENCE).as_posix()
        entry["snapshot_sha256"] = sha(snapshot_path.read_bytes())

        rc, shot = cli("screenshot", timeout=12)
        entry["screenshot_returncode"] = rc
        entry["screenshot_output"] = shot
        screenshot_match = re.search(r"Screenshot of viewport\]\(([^)]+\.png)\)", shot)
        screenshot_name = screenshot_match.group(1).replace("\\", "/") if screenshot_match else None
        screenshot_source = (EVIDENCE / screenshot_name).resolve() if screenshot_name else None
        screenshot_target = SCREENSHOTS / f"notice-{notice_id}.png"
        if screenshot_source and screenshot_source.is_file() and screenshot_source.is_relative_to(EVIDENCE.resolve()):
            write_once(screenshot_target, screenshot_source.read_bytes())
            entry["screenshot_file"] = screenshot_target.relative_to(EVIDENCE).as_posix()
            entry["screenshot_sha256"] = sha(screenshot_target.read_bytes())
        else:
            entry["screenshot_file"] = screenshot_name
            entry["screenshot_sha256"] = None
        entry["classification"] = "browser_rendered_content" if entry["article_text_present"] else "empty_or_unverified_browser_render"
        requests_rc, requests_output = cli("requests", timeout=12)
        entry["requests_returncode"] = requests_rc
        entry["requests_output"] = requests_output
        request_match = re.search(rf"(\d+)\. \[GET\] https://www\.csindex\.com\.cn/csindex-home/announcement/queryAnnouncementById\?id={re.escape(notice_id)} => \[(\d+)\]", requests_output)
        if request_match:
            request_number, status = request_match.groups()
            entry["detail_api_http_status"] = int(status)
            body_rc, response_body = cli("response-body", request_number, timeout=12)
            entry["detail_api_response_returncode"] = body_rc
            entry["detail_api_response"] = response_body
        entry["detail_403_observed"] = entry.get("detail_api_http_status") == 403
        entry["detail_200_observed"] = entry.get("detail_api_http_status") == 200
        if entry["detail_403_observed"] and not entry["article_text_present"]:
            entry["classification"] = "official_detail_api_403_blank_ui"
        elif entry["detail_200_observed"] and entry["article_text_present"]:
            entry["classification"] = "official_detail_api_200_article_rendered"
        persist_state(state)

    rc, close = cli("close")
    state["browser_close"] = {"returncode": rc, "output": close, "at_utc": datetime.now(UTC).isoformat()}
    state["completed_at_utc"] = datetime.now(UTC).isoformat()
    write_once(OUTPUT, (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    verify_panel("after_browser", PANEL_FINAL)
    print(json.dumps({
        "targets": len(state["results"]),
        "navigations": sum(item.get("navigation_returncode") == 0 for item in state["results"]),
        "browser_rendered": sum(item.get("article_text_present") is True for item in state["results"]),
        "output_sha256": sha(OUTPUT.read_bytes()),
        "panel_pending": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
