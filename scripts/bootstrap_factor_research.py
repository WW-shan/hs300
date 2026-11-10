#!/usr/bin/env python3
"""Bootstrap research pools and the watch-only core factor set."""
from __future__ import annotations

import json
from pathlib import Path

from app.factor_research.bootstrap import bootstrap_core_research

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def main() -> int:
    result = bootstrap_core_research(DATA)
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
