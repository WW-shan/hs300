from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factor_research.automation import (
    assert_portfolio_candidates_admitted,
    automation_research_id,
)

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
AUTOMATION = DATA / "factor_research" / "automation"
SCRIPT = ROOT / "scripts" / "research_factor_automation_p6.py"


def _latest() -> dict:
    return json.loads((AUTOMATION / "latest.json").read_text(encoding="utf-8"))


def _latest_payload() -> tuple[dict, dict]:
    latest = _latest()
    run = json.loads((DATA / latest["run_json"]).read_text(encoding="utf-8"))
    return latest, run


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _p6_module():
    spec = importlib.util.spec_from_file_location("research_factor_automation_p6", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_latest_audit_uses_generated_at_not_directory_order(tmp_path: Path) -> None:
    audits = tmp_path / "factor_research" / "audits"
    newest_id = "sha256:" + "0" * 64
    older_id = "sha256:" + "f" * 64
    for directory, audit_id, generated_at in (
        (newest_id.removeprefix("sha256:"), newest_id, "2026-11-11T00:00:00Z"),
        (older_id.removeprefix("sha256:"), older_id, "2026-11-10T00:00:00Z"),
    ):
        root = audits / directory
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(
            json.dumps({"audit_id": audit_id, "generated_at": generated_at}),
            encoding="utf-8",
        )
        (root / "findings.json").write_text("{}", encoding="utf-8")

    selected = _p6_module()._latest_audit(tmp_path)

    assert selected is not None
    assert selected["manifest"]["audit_id"] == newest_id


def test_published_p6_result_is_complete_and_watch_only() -> None:
    latest, payload = _latest_payload()

    assert latest["research_id"] == payload["research_id"]
    assert automation_research_id(payload) == payload["research_id"]
    assert payload["summary"]["active_version"] is None
    assert payload["summary"]["orders_generated"] is False
    assert payload["classification"]["unclassified_factor_ids"] == []
    assert [item["name"] for item in payload["ledger_bindings"]] == [
        "p1",
        "p2",
        "p3",
        "p4",
        "p5",
        "p8",
    ]
    p8 = next(item for item in payload["ledger_bindings"] if item["name"] == "p8")
    assert p8["research_id"] == "sha256:448cbd37b4faefc94025243be86b496779fcf649273d2e15b1a1835a42874038"
    assert latest["ledger_record_id"].startswith("lr_")


def test_p6_rerun_does_not_change_outputs_or_ledger() -> None:
    latest, _payload = _latest_payload()
    paths = [
        AUTOMATION / "latest.json",
        DATA / latest["run_json"],
        DATA / latest["run_markdown"],
        DATA / "factor_research" / "ledger" / "head.json",
        DATA / "factor_research" / "ledger" / "records.jsonl",
    ]
    before = _hash(paths)

    subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT / "backend",
        check=True,
        capture_output=True,
        text=True,
    )

    assert _hash(paths) == before


def test_p6_admission_guard_accepts_ready_and_rejects_blocked_factor() -> None:
    _latest, payload = _latest_payload()
    ready = next(
        factor_id
        for factor_id, row in payload["admission"].items()
        if row["portfolio_ready"] is True
    )
    blocked = next(
        factor_id
        for factor_id, row in payload["admission"].items()
        if row["portfolio_ready"] is False
    )

    admitted = assert_portfolio_candidates_admitted(DATA, [ready])
    assert admitted["research_id"] == payload["research_id"]
    with pytest.raises(ValueError, match="portfolio admission rejected"):
        assert_portfolio_candidates_admitted(DATA, [blocked])
