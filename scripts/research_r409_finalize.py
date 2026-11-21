#!/usr/bin/env python3
"""R409 finalize: leg attribution plus 3x stress record."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "factor_research" / "research" / "r409_leg_attribution_2026-11-22.json"


def main() -> int:
    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    payload["stress_3x"] = {
        "test_total_return": 0.2006,
        "test_sharpe": 1.01,
        "test_mdd": -0.1067,
        "note": "R403V2 3x point estimate; bootstrap p05 remains the governing tail result.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    REPORT.write_bytes(raw)
    REPORT.with_name(REPORT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {REPORT.name}\n", encoding="ascii")
    print("updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
