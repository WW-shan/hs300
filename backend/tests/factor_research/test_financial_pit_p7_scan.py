from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factor_research.fingerprints import sha256_json

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
PIT = DATA / "factor_research" / "pit"
SCRIPT = ROOT / "scripts" / "audit_financial_pit_p7.py"


def _hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_published_p7_audit_is_verified_and_content_addressed() -> None:
    latest = json.loads((PIT / "latest.json").read_text(encoding="utf-8"))
    payload = json.loads((DATA / latest["run_json"]).read_text(encoding="utf-8"))

    assert payload["status"] == "verified"
    assert len(payload["verified_factor_ids"]) == 15
    assert set(payload["verified_factor_ids"]) == set(payload["factors"])
    assert latest["research_id"] == payload["research_id"]
    assert sha256_json({
        key: value
        for key, value in payload.items()
        if key not in {"research_id", "generated_at"}
    }) == payload["research_id"]


def test_p7_promotes_only_audited_financial_taxonomy_records() -> None:
    latest = json.loads((PIT / "latest.json").read_text(encoding="utf-8"))
    payload = json.loads((DATA / latest["run_json"]).read_text(encoding="utf-8"))
    taxonomy = json.loads(
        (DATA / "factor_research" / "catalog" / "field_taxonomy.json").read_text(
            encoding="utf-8"
        )
    )
    records = {item["factor_id"]: item for item in taxonomy["records"]}

    for factor_id in payload["verified_factor_ids"]:
        assert records[factor_id]["pit_hint"] == "verified"
        assert records[factor_id]["future_data_status"] == "clear"
    assert records["uf_cash_quality"]["pit_hint"] == "declared"


@pytest.mark.skipif(
    not (DATA / "financials" / "metrics" / "part.parquet").exists(),
    reason="local financial source data is not present",
)
def test_p7_rerun_is_idempotent() -> None:
    latest = json.loads((PIT / "latest.json").read_text(encoding="utf-8"))
    paths = [
        PIT / "latest.json",
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
