#!/usr/bin/env python3
"""Freeze every pre-contract research runner by exact path and SHA-256."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "data/factor_research/research/research_contract_grandfather_2026-11-24.json"
)


def build_manifest(root: Path) -> dict[str, Any]:
    paths = sorted((root / "scripts").glob("research_*.py"))
    paths += sorted(
        (root / "data/strategies/ai/.iterations").rglob("evidence/run_*.py")
    )
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "disposition": "legacy_grandfathered",
            "reason": "Hashes frozen before the mandatory research contract took effect.",
        }
        for path in paths
    ]
    return {
        "schema_version": 1,
        "generated_at": "2026-11-24",
        "count": len(rows),
        "rows": rows,
    }


def main() -> int:
    payload = build_manifest(ROOT)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    OUTPUT.write_text(raw, encoding="utf-8")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    OUTPUT.with_name(f"{OUTPUT.name}.sha256").write_text(
        f"{digest}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({"output": OUTPUT.relative_to(ROOT).as_posix(), "count": payload["count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
