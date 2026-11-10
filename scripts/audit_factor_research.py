#!/usr/bin/env python3
"""Run a batch data audit against an extension table or local factor panel."""
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--panel", type=Path, help="parquet or csv panel with date/symbol columns")
    source.add_argument("--ext-data", help="extension config id; scans data/ext_data/<id>/timeseries partitions")
    parser.add_argument("--factor", action="append", dest="factors", help="factor id; repeatable")
    parser.add_argument("--policy", default="default_v1")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.panel is not None and not args.factors:
        parser.error("--factor is required with --panel")

    service = AuditService(DATA, load_policy(DATA, args.policy))
    if args.ext_data:
        result = service.audit_ext_data(args.ext_data, args.factors)
    else:
        if args.panel.suffix.lower() == ".csv":
            panel = pl.read_csv(args.panel, try_parse_dates=True)
        else:
            panel = pl.read_parquet(args.panel)
        result = service.audit_panel(
            panel,
            args.factors,
            input_fingerprints={"panel": str(args.panel)},
        )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    counts: dict[str, int] = {}
    for finding in result.findings.values():
        key = str(finding["integrity"])
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({
        "audit_id": result.audit_id,
        "factor_count": len(result.factors),
        "integrity_counts": counts,
        "generated_at": result.generated_at,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
