"""Append-only research ledger with a deterministic hash chain."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.factor_research.fingerprints import content_hash, hash_token, utc_now
from app.factor_research.locking import FileLock, atomic_write_json
from app.factor_research.models import LedgerHead, LedgerRecord


class LedgerCorruptError(RuntimeError):
    """The ledger cannot be trusted as a complete, ordered chain."""


_ZERO_HASH = "sha256:" + ("0" * 64)
_RESERVED = frozenset({
    "schema_version", "sequence", "recorded_at", "actor", "operation",
    "ledger_prev_hash", "record_id", "record_hash",
})


class ResearchLedger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "factor_research"
        self.path = self.root / "ledger" / "records.jsonl"
        self.head_path = self.root / "ledger" / "head.json"
        self.schema_path = self.root / "ledger" / "schema.json"
        self.lock_path = self.root / ".write.lock"

    def _record_hash(self, payload: Mapping[str, Any]) -> str:
        return content_hash(payload, "record_id", "record_hash")

    def _make_id(self, record_hash: str) -> str:
        return f"lr_{hash_token(record_hash)[:24]}"

    def records(self) -> list[LedgerRecord]:
        self.verify()
        if not self.path.exists():
            return []
        out: list[LedgerRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(LedgerRecord.model_validate(json.loads(line)))
        return out

    def verify(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_text(encoding="utf-8")
        if raw and not raw.endswith("\n"):
            raise LedgerCorruptError("ledger has a non-terminated final record")
        expected_sequence = 1
        previous = _ZERO_HASH
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                raise LedgerCorruptError(f"ledger blank line at line {line_number}")
            try:
                payload = json.loads(line)
                record = LedgerRecord.model_validate(payload)
            except Exception as exc:
                raise LedgerCorruptError(f"invalid ledger line {line_number}: {exc}") from exc
            if record.sequence != expected_sequence:
                raise LedgerCorruptError(
                    f"ledger sequence mismatch at line {line_number}: "
                    f"expected {expected_sequence}, got {record.sequence}"
                )
            if record.ledger_prev_hash != previous:
                raise LedgerCorruptError(f"ledger prev hash mismatch at line {line_number}")
            expected_hash = self._record_hash(record.model_dump(mode="json"))
            if record.record_hash != expected_hash:
                raise LedgerCorruptError(f"ledger record hash mismatch at line {line_number}")
            if record.record_id != self._make_id(record.record_hash):
                raise LedgerCorruptError(f"ledger record id mismatch at line {line_number}")
            previous = record.record_hash
            expected_sequence += 1

    def append(
        self,
        operation: str,
        *,
        payload: Mapping[str, Any] | None = None,
        actor: str = "research",
    ) -> LedgerRecord:
        payload = dict(payload or {})
        reserved = sorted(set(payload).intersection(_RESERVED))
        if reserved:
            raise ValueError(f"ledger payload cannot override reserved fields: {reserved}")

        with FileLock(self.lock_path):
            existing = self.records()
            sequence = (existing[-1].sequence + 1) if existing else 1
            prev = existing[-1].record_hash if existing else _ZERO_HASH
            # Include every optional model default in the hashable payload.
            # Otherwise append hashes a sparse dict while verification hashes a
            # model dump with defaults, producing a false corruption alarm.
            base: dict[str, Any] = {
                "schema_version": 1,
                "sequence": sequence,
                "recorded_at": utc_now(),
                "actor": actor,
                "operation": operation,
                "set_id": None,
                "set_version": None,
                "pool_id": None,
                "pool_revision": None,
                "factor_id": None,
                "factor_version": None,
                "catalog_snapshot_id": None,
                "evidence_refs": [],
                "input_fingerprints": {},
                "metrics": {},
                "verdict": None,
                "reason": None,
                "negative_knowledge": None,
                "supersedes_record_id": None,
                **payload,
                "ledger_prev_hash": prev,
            }
            record_hash = self._record_hash(base)
            base["record_hash"] = record_hash
            base["record_id"] = self._make_id(record_hash)
            # Pydantic validates all optional fields and rejects unknown payload keys.
            record = LedgerRecord.model_validate(base)
            text = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if not self.schema_path.exists():
                atomic_write_json(self.schema_path, {
                    "schema_version": 1,
                    "description": "Append-only factor research ledger",
                })
            atomic_write_json(self.head_path, self.head().model_dump(mode="json"))
            return record

    def head(self) -> LedgerHead:
        records = self.records()
        if not records:
            return LedgerHead(last_sequence=0, last_record_hash=_ZERO_HASH)
        last = records[-1]
        return LedgerHead(
            last_sequence=last.sequence,
            last_record_hash=last.record_hash,
            updated_at=last.recorded_at,
        )
