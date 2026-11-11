from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factor_research.automation import (
    audit_research_bindings,
    bind_research_evidence,
)
from app.factor_research.ledger import ResearchLedger


def _evidence(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "factor_research" / "automation" / "runs"
    root.mkdir(parents=True)
    output = root / "run.json"
    markdown = root / "run.md"
    output.write_text(json.dumps({"research_id": "sha256:" + "1" * 64}), encoding="utf-8")
    markdown.write_text("# run\n", encoding="utf-8")
    return output, markdown


def test_bind_research_evidence_is_idempotent(tmp_path: Path) -> None:
    output, markdown = _evidence(tmp_path)
    research_id = "sha256:" + "1" * 64
    payload = {"metrics": {"pending_tasks": 0}, "verdict": "watch_only"}

    first, created = bind_research_evidence(
        tmp_path,
        operation="factor_research_automation_published",
        evidence_paths=[output, markdown],
        research_id=research_id,
        payload=payload,
    )
    second, created_again = bind_research_evidence(
        tmp_path,
        operation="factor_research_automation_published",
        evidence_paths=[output, markdown],
        research_id=research_id,
        payload=payload,
    )

    assert first == second
    assert created is True
    assert created_again is False
    assert len(ResearchLedger(tmp_path).records()) == 1


def test_changed_evidence_hash_fails_closed(tmp_path: Path) -> None:
    output, markdown = _evidence(tmp_path)
    research_id = "sha256:" + "1" * 64
    bind_research_evidence(
        tmp_path,
        operation="factor_research_automation_published",
        evidence_paths=[output, markdown],
        research_id=research_id,
        payload={"metrics": {}},
    )
    markdown.write_text("# changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="research evidence hash mismatch"):
        bind_research_evidence(
            tmp_path,
            operation="factor_research_automation_published",
            evidence_paths=[output, markdown],
            research_id=research_id,
            payload={"metrics": {}},
        )


def test_audit_research_bindings_returns_existing_record(tmp_path: Path) -> None:
    output, markdown = _evidence(tmp_path)
    research_id = "sha256:" + "1" * 64
    record, _created = bind_research_evidence(
        tmp_path,
        operation="factor_research_automation_published",
        evidence_paths=[output, markdown],
        research_id=research_id,
        payload={"metrics": {}},
    )

    audited = audit_research_bindings(
        tmp_path,
        [
            {
                "name": "p6",
                "operation": "factor_research_automation_published",
                "research_id": research_id,
                "evidence_paths": [output, markdown],
            }
        ],
    )

    assert audited[0]["record_id"] == record.record_id
    assert audited[0]["record_hash"] == record.record_hash


def test_same_evidence_cannot_bind_to_second_research_id(tmp_path: Path) -> None:
    output, markdown = _evidence(tmp_path)
    bind_research_evidence(
        tmp_path,
        operation="factor_research_automation_published",
        evidence_paths=[output, markdown],
        research_id="sha256:" + "1" * 64,
        payload={"metrics": {}},
    )

    with pytest.raises(ValueError, match="already bound"):
        bind_research_evidence(
            tmp_path,
            operation="factor_research_automation_published",
            evidence_paths=[output, markdown],
            research_id="sha256:" + "2" * 64,
            payload={"metrics": {}},
        )
