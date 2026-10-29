from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
MANIFEST = EVIDENCE / "r298_input_manifest.jsonl"
ADDENDUM = EVIDENCE / "r298_manifest_execution_addendum.json"
PREREG = EVIDENCE / "r298_preregistration.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    value = digest(data)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    manifest_bytes = MANIFEST.read_bytes()
    addendum_bytes = ADDENDUM.read_bytes()
    addendum = json.loads(addendum_bytes)
    declared = {item["path"]: item for item in addendum["execution_script_changes"]}
    entries = [json.loads(line) for line in manifest_bytes.decode("utf-8").splitlines() if line.strip()]
    unchanged: list[str] = []
    exceptions: list[dict] = []
    other: list[dict] = []
    for item in entries:
        path = ROOT / Path(item["path"].replace(chr(92), "/"))
        stat = path.stat()
        actual_hash = digest(path.read_bytes())
        if stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"] and actual_hash == item["sha256"]:
            unchanged.append(item["path"])
            continue
        row = {
            "path": item["path"],
            "original_sha256": item["sha256"],
            "current_sha256": actual_hash,
            "original_bytes": item["bytes"],
            "current_bytes": stat.st_size,
            "original_mtime_ns": item["mtime_ns"],
            "current_mtime_ns": stat.st_mtime_ns,
        }
        amended = declared.get(item["path"])
        if (
            amended
            and amended.get("original_sha256") == item["sha256"]
            and amended.get("current_sha256") == actual_hash
            and amended.get("current_bytes") == stat.st_size
            and amended.get("current_mtime_ns") == stat.st_mtime_ns
        ):
            exceptions.append(row)
        else:
            other.append(row)

    addendum_sidecar = ADDENDUM.with_name(ADDENDUM.name + ".sha256").read_text(encoding="ascii").split()[0]
    integrity = {
        "round": "R298",
        "classification": "frozen_input_manifest_final_integrity_recheck",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_path": "evidence/r298_input_manifest.jsonl",
        "manifest_sha256": digest(manifest_bytes),
        "manifest_sidecar_sha256": MANIFEST.with_name(MANIFEST.name + ".sha256").read_text(encoding="ascii").split()[0],
        "manifest_count": len(entries),
        "verified_unchanged_count": len(unchanged),
        "documented_execution_script_exception_count": len(exceptions),
        "documented_execution_script_exceptions": exceptions,
        "other_differences": other,
        "addendum_path": "evidence/r298_manifest_execution_addendum.json",
        "addendum_sha256": digest(addendum_bytes),
        "addendum_matches_sidecar": digest(addendum_bytes) == addendum_sidecar,
        "addendum_declared_exception_count": len(declared),
        "pass": (
            len(entries) == 10713
            and len(unchanged) == 10710
            and len(exceptions) == 3
            and not other
            and set(declared) == {row["path"] for row in exceptions}
        ),
    }
    integrity_path = EVIDENCE / "r298_final_integrity_audit.json"
    integrity_hash = write_once(
        integrity_path,
        (json.dumps(integrity, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )

    prereg_bytes = PREREG.read_bytes()
    prereg_hash = digest(prereg_bytes)
    prereg_sidecar = PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]
    if prereg_hash != prereg_sidecar:
        raise RuntimeError("preregistration checksum mismatch")
    prereg = json.loads(prereg_bytes)
    candidate = prereg["panel_candidate"]
    request = urllib.request.Request(
        "http://127.0.0.1:3018/api/backtest/candidates",
        method="GET",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        status = response.status
    items = json.loads(raw).get("items", [])
    expected = candidate["config"]

    def matches(item: dict) -> bool:
        config = item.get("config", {})
        return (
            item.get("kind") == candidate["kind"]
            and item.get("name") == candidate["name"]
            and item.get("source_id") == candidate["source_id"]
            and config.get("start") == expected.get("start")
            and config.get("end") == expected.get("end")
        )

    matches_found = [item for item in items if matches(item)]
    prior = json.loads((EVIDENCE / "r298_panel_registration.json").read_text(encoding="utf-8"))
    guard_ids = {label: row["id"] for label, row in prior["guard_candidates_after"].items() if row}
    guard_rows = {label: [item for item in items if item.get("id") == item_id] for label, item_id in guard_ids.items()}
    readback = {
        "round": "R298",
        "classification": "final_read_only_panel_status_recheck",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": "http://127.0.0.1:3018",
        "endpoint": "/api/backtest/candidates",
        "method": "GET",
        "write_requests_sent": 0,
        "http_status": status,
        "response_sha256": digest(raw),
        "response_item_count": len(items),
        "response_items": items,
        "r298_match_count": len(matches_found),
        "r298_matches": matches_found,
        "expected_r298_id": "01aae1c6f0314612bec472bdd3140a69",
        "guard_ids": guard_ids,
        "guard_match_counts": {label: len(rows) for label, rows in guard_rows.items()},
        "guard_statuses": {label: [row.get("status") for row in rows] for label, rows in guard_rows.items()},
        "preregistration_sha256": prereg_hash,
        "prior_registration_proof_sha256": digest((EVIDENCE / "r298_panel_registration.json").read_bytes()),
        "pass": (
            status == 200
            and len(items) == 15
            and len(matches_found) == 1
            and matches_found[0].get("id") == "01aae1c6f0314612bec472bdd3140a69"
            and matches_found[0].get("status") == "pending"
            and len(guard_ids) == 10
            and all(len(guard_rows[label]) == 1 and guard_rows[label][0].get("status") == "pending" for label in guard_rows)
        ),
    }
    readback_path = EVIDENCE / "r298_panel_final_readback.json"
    readback_hash = write_once(
        readback_path,
        (json.dumps(readback, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "manifest_audit": {
            "path": integrity_path.name, "sha256": integrity_hash,
            "count": len(entries), "unchanged": len(unchanged),
            "documented_exceptions": len(exceptions), "other_differences": other,
            "pass": integrity["pass"],
        },
        "panel_readback": {
            "path": readback_path.name, "sha256": readback_hash,
            "http_status": status, "candidate_count": len(items),
            "r298_match_count": len(matches_found),
            "r298_id": matches_found[0].get("id") if matches_found else None,
            "r298_status": matches_found[0].get("status") if matches_found else None,
            "guards_pending": all(len(guard_rows[label]) == 1 and guard_rows[label][0].get("status") == "pending" for label in guard_rows),
            "pass": readback["pass"],
        },
    }, ensure_ascii=False, indent=2))
    return 0 if integrity["pass"] and readback["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
