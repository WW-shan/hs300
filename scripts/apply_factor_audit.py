#!/usr/bin/env python3
"""Apply an audited factor integrity result to research pools and watch versions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.factor_research.workflow import apply_factor_audit

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-id", required=True)
    args = parser.parse_args()
    result = apply_factor_audit(DATA, args.audit_id)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
