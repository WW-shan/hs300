from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[5]
CLI = Path(os.environ["LOCALAPPDATA"]) / "npm-cache/_npx/31e32ef8478fbf80/node_modules/@playwright/cli/playwright-cli.js"
SESSION = "r304-a4"
OUTPUT = EVIDENCE / "r304_A4_14225_postload_addendum"


def run_cli(*args: str) -> str:
    env = os.environ.copy()
    env["PLAYWRIGHT_CLI_SESSION"] = SESSION
    result = subprocess.run(
        ["node", str(CLI), *args], cwd=REPO, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"CLI {args[0]} failed ({result.returncode}): {result.stdout}{result.stderr}")
    return result.stdout + result.stderr


def write_once(path: Path, content: str | bytes) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError("Addendum directory exists; refusing to overwrite evidence")
    OUTPUT.mkdir()
    requests = run_cli("requests")
    match = re.search(r"^1\. \[GET\] https://www\.csindex\.com\.cn/csindex-home/announcement/queryAnnouncementById\?id=14225 => \[(\d+)\]", requests, re.M)
    if not match or match.group(1) != "200":
        raise RuntimeError("The already observed 14225 request is not the expected HTTP 200; no navigation will occur")
    body = run_cli("eval", "document.body.innerText")
    snap = run_cli("snapshot")
    api = run_cli("response-body", "1")
    screenshot = run_cli("screenshot")
    screenshot_match = re.search(r"Screenshot of viewport\]\(([^)]+\.png)\)", screenshot)
    if not screenshot_match:
        raise RuntimeError("Could not identify the screenshot path; captured page remains untouched")
    source = (REPO / screenshot_match.group(1)).resolve()
    if not source.is_file() or not source.is_relative_to(REPO):
        raise RuntimeError("Screenshot output path is missing or outside repository")

    artifacts = {
        "requests.txt": requests,
        "body_text.txt": body,
        "snapshot.txt": snap,
        "detail_api_response.txt": api,
    }
    hashes = {name: write_once(OUTPUT / name, value) for name, value in artifacts.items()}
    shot_target = OUTPUT / "screenshot.png"
    with source.open("rb") as src, shot_target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    shot_hash = hashlib.sha256(shot_target.read_bytes()).hexdigest()
    with (OUTPUT / "screenshot.png.sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{shot_hash}  screenshot.png\n")
    manifest = {
        "round": "R304-A4",
        "notice_id": "14225",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "capture_type": "read-only post-load completion of the existing single navigation; no goto/reload performed",
        "target_url": "https://www.csindex.com.cn/#/about/newsDetail?id=14225",
        "detail_api_url": "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementById?id=14225",
        "detail_api_http_status": 200,
        "detail_api_mime": "application/json",
        "article_title": "上交所及中证指数定期调整沪深300、上证50、科创50和中证500等指数样本",
        "published_at": "2022-05-27",
        "stated_effective_at": "2022-06-10 收市后",
        "hs300_event": "沪深300指数更换28只样本",
        "sha256": {**hashes, "screenshot.png": shot_hash},
    }
    write_once(OUTPUT / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
