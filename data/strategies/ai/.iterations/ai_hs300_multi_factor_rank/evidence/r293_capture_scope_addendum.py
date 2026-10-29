#!/usr/bin/env python3
"""Freeze the source files omitted from the original R293 scope manifest."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
SOURCES = [
    "backend/app/main.py",
    "backend/app/services/ext_pull.py",
    "backend/app/services/ext_data.py",
    "backend/app/services/ext_presets.py",
    "backend/app/api/ext_data.py",
    "backend/app/tickflow/repository.py",
    "backend/app/services/strategy_cache.py",
    "backend/app/services/fs_utils.py",
    "backend/app/factors/ext_factors.py",
    "data/ext_data/alt_hs300/config.json",
    "data/ext_data/alt_hs300_minrank/config.json",
    "data/ext_data/alt_hs300_qrs/config.json",
]
BUILDER_NAMES = re.compile(
    r"build_alt_factor_data|build_r253_min_rank_factor|"
    r"build_r257_quality_relative_strength_factor|probe_r257_quality_relative_strength"
)
SEARCH_ROOTS = (ROOT / "backend/app", ROOT / "backend/tests", ROOT / "scripts")
SEARCH_PATH = EVIDENCE / "r293_scope_addendum_search.txt"
MANIFEST_PATH = EVIDENCE / "r293_scope_addendum.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    digest = sha(data)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def main() -> int:
    outputs = (SEARCH_PATH, MANIFEST_PATH)
    if any(path.exists() or Path(str(path) + ".sha256").exists() for path in outputs):
        raise RuntimeError("R293 scope addendum already exists; refusing to overwrite")

    source_rows = []
    for relative in SOURCES:
        path = ROOT / relative
        data = path.read_bytes()
        row = {"path": relative, "size_bytes": len(data), "sha256": sha(data)}
        if relative.endswith("/config.json"):
            config = json.loads(data)
            pull = config.get("pull")
            row["config_id"] = config.get("id")
            row["pull_present"] = pull is not None
            row["pull_enabled"] = bool((pull or {}).get("enabled", False))
        source_rows.append(row)

    hits = []
    for base in SEARCH_ROOTS:
        for path in sorted(base.rglob("*.py")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, start=1):
                if BUILDER_NAMES.search(line):
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}:{line.strip()}")
    search_data = ("\n".join(hits) + ("\n" if hits else "")).encode("utf-8")
    search_hash = write_once(SEARCH_PATH, search_data)

    payload = {
        "round": "R293",
        "classification": "post-freeze_scope_addendum",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "reason": (
            "The original R293 manifest did not include the extension pull scheduler, app startup, "
            "configuration/data APIs, repository and strategy-cache invalidation, parquet writer, "
            "factor cache registration, or the three local extension configurations. Original "
            "frozen artifacts are preserved unchanged; this addendum expands audit coverage."
        ),
        "source_files": source_rows,
        "builder_reference_search": {
            "roots": [path.relative_to(ROOT).as_posix() for path in SEARCH_ROOTS],
            "identifiers": BUILDER_NAMES.pattern,
            "hit_count": len(hits),
            "output": SEARCH_PATH.name,
            "sha256": search_hash,
        },
        "limitations": [
            "This is a source/configuration audit, not a runtime builder or cache test.",
            "Configuration hashes preserve local file identity; no credentials are recorded separately.",
            "It does not establish PIT validity, historical factor values, returns, or alpha.",
        ],
    }
    manifest_hash = write_once(
        MANIFEST_PATH,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "round": "R293",
        "supplementary_source_files": len(source_rows),
        "builder_reference_hits": len(hits),
        "addendum_sha256": manifest_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
