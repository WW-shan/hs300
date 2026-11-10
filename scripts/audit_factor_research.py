#!/usr/bin/env python3
"""Run a batch data audit against a local parquet/csv factor panel."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from app.factor_research.audit import AuditService
from app.factor_research.policies import load_policy

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path, help="parquet or csv panel with date/symbol columns")
    parser.add_argument("--factor", action="append", required=True, dest="factors")
    parser.add_argument("--policy", default="default_v1")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.panel.suffix.lower() == ".csv":
        panel = pl.read_csv(args.panel, try_parse_dates=True)
    else:
        panel = pl.read_parquet(args.panel)
    result = AuditService(DATA, load_policy(DATA, args.policy)).audit_panel(
        panel,
        args.factors,
        input_fingerprints={"panel": str(args.panel)},
    )
    if args.output:
        args.output.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "audit_id": result.audit_id,
        "factors": list(result.factors),
        "generated_at": result.generated_at,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
