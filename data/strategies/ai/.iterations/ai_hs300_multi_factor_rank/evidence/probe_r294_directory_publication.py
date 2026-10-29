#!/usr/bin/env python3
"""Probe process-level directory publication rollback using synthetic files only."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
STAGING_ROOT = (ROOT / "data" / ".staging").resolve()
EVIDENCE = Path(__file__).resolve().parent
RESULT = EVIDENCE / "r294_publication_probe.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha(payload)
    path.with_name(path.name + ".sha256").open("x", encoding="ascii").write(
        f"{digest}  {path.name}\n"
    )
    return digest


def write_tree(path: Path, name: str, content: bytes) -> None:
    path.mkdir(parents=True, exist_ok=False)
    (path / "marker.txt").write_bytes(content)
    (path / "nested").mkdir()
    (path / "nested" / name).write_bytes(content + b"/nested")


def read_tree(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha(item.read_bytes())
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def main() -> int:
    if RESULT.exists() or Path(str(RESULT) + ".sha256").exists():
        raise RuntimeError("R294 probe output exists; refusing to overwrite")
    if not STAGING_ROOT.is_dir():
        raise RuntimeError("Expected existing data/.staging directory; no fallback path is allowed")

    probe = {
        "round": "R294",
        "classification": "synthetic_same_volume_directory_switch_probe",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "staging_root": STAGING_ROOT.relative_to(ROOT).as_posix(),
        "same_volume": os.stat(STAGING_ROOT).st_dev == os.stat(ROOT / "data").st_dev,
        "production_paths_touched": False,
    }
    if not probe["same_volume"]:
        raise RuntimeError("Staging root is not on the same filesystem volume")

    with tempfile.TemporaryDirectory(prefix="r294_publication_", dir=STAGING_ROOT) as temp_name:
        temp = Path(temp_name).resolve()
        if not temp.is_relative_to(STAGING_ROOT):
            raise RuntimeError("Temporary probe path escaped data/.staging")

        success = temp / "success"
        live = success / "live"
        staged = success / "staged"
        backup = success / "backup"
        success.mkdir()
        write_tree(live, "generation.txt", b"old-complete-generation")
        write_tree(staged, "generation.txt", b"new-complete-generation")
        old_expected = read_tree(live)
        new_expected = read_tree(staged)
        os.replace(live, backup)
        os.replace(staged, live)
        success_case = {
            "passed": read_tree(live) == new_expected and read_tree(backup) == old_expected,
            "new_tree_complete": read_tree(live) == new_expected,
            "old_tree_retained_for_rollback": read_tree(backup) == old_expected,
            "live_files": read_tree(live),
            "backup_files": read_tree(backup),
        }
        if not success_case["passed"]:
            raise RuntimeError("Successful directory switch did not preserve complete trees")

        rollback = temp / "rollback"
        live = rollback / "live"
        staged = rollback / "staged"
        backup = rollback / "backup"
        rollback.mkdir()
        write_tree(live, "generation.txt", b"rollback-old-generation")
        write_tree(staged, "generation.txt", b"rollback-new-generation")
        old_expected = read_tree(live)
        staged_expected = read_tree(staged)
        os.replace(live, backup)
        injected = False
        try:
            raise RuntimeError("injected failure before staged-to-live rename")
        except RuntimeError as error:
            if str(error) != "injected failure before staged-to-live rename":
                raise
            injected = True
            if backup.exists() and not live.exists():
                os.replace(backup, live)
        rollback_case = {
            "failure_injected": injected,
            "old_live_tree_restored": live.exists() and read_tree(live) == old_expected,
            "staged_tree_preserved": staged.exists() and read_tree(staged) == staged_expected,
            "backup_cleared_by_restore": not backup.exists(),
            "live_files": read_tree(live),
            "staged_files": read_tree(staged),
        }
        rollback_case["passed"] = all(
            rollback_case[key]
            for key in (
                "failure_injected",
                "old_live_tree_restored",
                "staged_tree_preserved",
                "backup_cleared_by_restore",
            )
        )
        if not rollback_case["passed"]:
            raise RuntimeError("Injected-failure rollback check failed")

    probe["success_case"] = success_case
    probe["rollback_case"] = rollback_case
    probe["overall_pass"] = success_case["passed"] and rollback_case["passed"]
    probe["limitations"] = [
        "Synthetic text files only; no parquet, production data, or running application readers were involved.",
        "The rollback path is tested only for a caught process-level exception, not process kill, OS crash, or power loss.",
        "Three factor trees still require multiple namespace changes; this probe does not prove a multi-tree transaction.",
        "No cache, DuckDB view, or API reader was running in this probe.",
    ]
    digest = write_once(
        RESULT,
        (json.dumps(probe, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"result": probe, "sha256": digest}, ensure_ascii=False, indent=2))
    return 0 if probe["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
