#!/usr/bin/env python3
"""Synchronize the factor mother-set snapshot and latest pointer."""
from __future__ import annotations

import json
from pathlib import Path

from app.factor_research.catalog import build_catalog, save_catalog
from app.factor_research.ledger import ResearchLedger
from app.factors.store import load_into_registry

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    load_into_registry(DATA)
    snapshot = build_catalog(DATA)
    path = save_catalog(DATA, snapshot)
    ledger = ResearchLedger(DATA)
    if not any(
        item.operation == "catalog_published" and item.catalog_snapshot_id == snapshot.snapshot_id
        for item in ledger.records()
    ):
        ledger.append(
            "catalog_published",
            payload={"catalog_snapshot_id": snapshot.snapshot_id, "reason": "sync catalog"},
        )
    print(json.dumps({
        "snapshot_id": snapshot.snapshot_id,
        "factor_count": snapshot.factor_count,
        "source_completeness": snapshot.source_completeness,
        "path": str(path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
