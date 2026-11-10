from __future__ import annotations

from pathlib import Path

import pytest

from app.factor_research.ledger import LedgerCorruptError, ResearchLedger


def test_ledger_append_hash_chain(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path)
    first = ledger.append("catalog_published", payload={"catalog_snapshot_id": "sha256:a"})
    second = ledger.append("audit_completed", payload={"set_id": "core_v1", "set_version": 1})
    assert second.ledger_prev_hash == first.record_hash
    ledger.verify()
    records = ledger.records()
    assert [item.sequence for item in records] == [1, 2]
    assert ledger.head().last_record_hash == second.record_hash


def test_ledger_rejects_corrupt_middle_line(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path)
    ledger.append("catalog_published", payload={"catalog_snapshot_id": "sha256:a"})
    path = tmp_path / "factor_research" / "ledger" / "records.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    with pytest.raises(LedgerCorruptError):
        ledger.verify()


def test_ledger_rejects_reserved_fields_from_payload(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path)
    with pytest.raises(ValueError):
        ledger.append("audit_completed", payload={"sequence": 99})
