from __future__ import annotations

import hashlib
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
    ]
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


def test_p6_admission_guard_accepts_ready_and_rejects_stale_audit() -> None:
    _latest, payload = _latest_payload()
    ready = next(
        factor_id
        for factor_id, row in payload["admission"].items()
        if row["portfolio_ready"] is True
    )
    stale = next(
        factor_id
        for factor_id, row in payload["admission"].items()
        if row["audit_status"] == "audit_stale"
    )

    admitted = assert_portfolio_candidates_admitted(DATA, [ready])
    assert admitted["research_id"] == payload["research_id"]
    with pytest.raises(ValueError, match="audit_stale"):
        assert_portfolio_candidates_admitted(DATA, [stale])
