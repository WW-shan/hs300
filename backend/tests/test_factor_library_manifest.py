from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
E = ROOT / "data/factor_research/catalog"
JSON = E / "library_manifest_2026-11-22.json"
MD = E / "library_manifest_2026-11-22.md"


def test_manifest_counts_and_checksums() -> None:
    payload = json.loads(JSON.read_text(encoding="utf-8"))
    assert payload["counts"]["total"] == 235
    assert payload["counts"]["with_research_evidence"] == 118
    assert payload["counts"]["incremental_pass"] == 11
    for path in (JSON, MD):
        sidecar = path.with_name(path.name + ".sha256").read_text(encoding="ascii").split()[0]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sidecar


def test_manifest_does_not_infer_missing_evidence_as_zero() -> None:
    payload = json.loads(JSON.read_text(encoding="utf-8"))
    assert any(row["research"]["ic_5d_full"] is None for row in payload["records"])
    assert all("library_state" in row for row in payload["records"])


def test_manifest_future_data_status_matches_latest_catalog() -> None:
    payload = json.loads(JSON.read_text(encoding="utf-8"))
    catalog_dir = ROOT / "data/factor_research/catalog"
    latest = json.loads((catalog_dir / "latest.json").read_text(encoding="utf-8"))
    snapshot = json.loads(
        (catalog_dir / "snapshots" / f"{latest['snapshot_id'].removeprefix('sha256:')}.json").read_text(
            encoding="utf-8"
        )
    )
    status_by_id = {row["factor_id"]: row["future_data_status"] for row in snapshot["factors"]}
    assert all(
        row["future_data_status"] == status_by_id[row["factor_id"]]
        for row in payload["records"]
        if row["factor_id"] in status_by_id
    )
