#!/usr/bin/env python3
"""Freeze the code and scope for R293's read-only rebuild-path audit."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
SOURCES = [
    "AGENTS.md", "CONTRIBUTING.md", "docs/secondary-development.md",
    "backend/app/api/kline.py", "backend/app/api/pipeline.py",
    "backend/app/jobs/daily_pipeline.py", "backend/app/services/kline_sync.py",
    "backend/app/services/repair_daily.py", "backend/app/services/extend_history.py",
    "backend/app/services/financial_sync.py", "backend/app/indicators/pipeline.py",
    "backend/app/factors/ext_factors.py", "backend/app/services/ext_data.py",
    "scripts/build_alt_factor_data.py", "scripts/build_r253_min_rank_factor.py",
    "scripts/build_r257_quality_relative_strength_factor.py",
    "scripts/probe_r257_quality_relative_strength.py",
    "backend/tests/test_daily_sync_cache_refresh.py", "backend/tests/test_pipeline_capacity.py",
    "backend/tests/test_build_alt_factor_data.py", "backend/tests/test_r253_min_rank_factor.py",
    "backend/tests/test_r257_quality_relative_strength_factor.py",
]
SEARCH_ROOTS = (ROOT / "backend/app", ROOT / "backend/tests", ROOT / "scripts")
BUILDER_NAMES = re.compile(
    r"build_alt_factor_data|build_r253_min_rank_factor|"
    r"build_r257_quality_relative_strength_factor|probe_r257_quality_relative_strength"
)
SEARCH_PATH = EVIDENCE / "r293_builder_reference_search.txt"
SNAPSHOT_PATH = EVIDENCE / "r293_input_snapshot.json"
PREREG_PATH = EVIDENCE / "r293_preregistration.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_once(path: Path, data: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(data)
    value = digest(data)
    with path.with_name(path.name + ".sha256").open("x", encoding="ascii") as stream:
        stream.write(f"{value}  {path.name}\n")
    return value


def main() -> int:
    outputs = (SEARCH_PATH, SNAPSHOT_PATH, PREREG_PATH)
    if any(path.exists() or Path(str(path) + ".sha256").exists() for path in outputs):
        raise RuntimeError("R293 freeze evidence already exists; refusing to overwrite")

    source_rows = []
    for relative in SOURCES:
        data = (ROOT / relative).read_bytes()
        source_rows.append({"path": relative, "size_bytes": len(data), "sha256": digest(data)})

    hits = []
    for base in SEARCH_ROOTS:
        for path in sorted(base.rglob("*.py")):
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if BUILDER_NAMES.search(line):
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{number}:{line.strip()}")
    search_bytes = ("\n".join(hits) + ("\n" if hits else "")).encode("utf-8")
    search_hash = write_once(SEARCH_PATH, search_bytes)

    snapshot = {
        "round": "R293",
        "classification": "frozen_code_input_manifest",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Read-only source audit of scheduled, manual, repair, and history-extension refresh paths, factor-builder reachability, output scope, publication behavior, and cache invalidation.",
        "source_files": source_rows,
        "builder_reference_search": {
            "roots": [path.relative_to(ROOT).as_posix() for path in SEARCH_ROOTS],
            "identifiers": BUILDER_NAMES.pattern,
            "hit_count": len(hits),
            "output": SEARCH_PATH.name,
            "sha256": search_hash,
        },
    }
    snapshot_hash = write_once(
        SNAPSHOT_PATH, (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )
    preregistration = {
        "round": "R293",
        "status": "frozen_before_results",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "research_question": "Do any scheduled, manual, repair, financial-sync, or history-extension application paths automatically rebuild the R257 dependency chain after daily/enriched updates, and what are the builders' write/cache side effects?",
        "hypothesis": "The application refreshes enriched data through several paths, while alt_hs300, R253, and R257 builders remain separate offline scripts; a base rebuild can remove the live partition tree before the full replacement is ready.",
        "method": [
            "Trace scheduler registration and execution, pipeline stages, public refresh/repair endpoints, direct daily K-line endpoints, financial-share recomputation, and history extension.",
            "Search application and test sources for direct or indirect builder invocation; inspect imports and subprocess/runpy invocation paths.",
            "Inspect each builder's inputs, date/universe scope, delete/overwrite semantics, configuration writes, and extension/strategy cache invalidation.",
            "Inspect related tests as source evidence only; do not execute tests or builders.",
        ],
        "decision_rules": [
            "Classify a path as automatic only if reachable in application code from a scheduler/API/job entry without a separate operator command.",
            "Distinguish K-line persistence, enriched recomputation, and each extension factor builder.",
            "Report exact write scopes and whether publication is per-file atomic or whole-dataset atomic.",
            "Do not infer historical PIT, old-value reproduction, returns, or alpha from this source audit.",
        ],
        "exclusions": [
            "No provider/network data pulls, backtest, screening, matching, paper trade, or order creation.",
            "No factor builder execution, no reads of factor values or returns, and no writes to production market/enriched/extension data or caches.",
            "No code changes, gate relaxation, strategy status change, or production activation.",
        ],
        "input_snapshot": {"file": SNAPSHOT_PATH.name, "sha256": snapshot_hash},
        "freeze_script_sha256": digest(Path(__file__).read_bytes()),
        "panel_candidate": {
            "kind": "strategy",
            "name": "R293 派生因子刷新调用链只读审计",
            "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
            "config": {
                "asset_type": "stock", "commission_pct": 0.0006,
                "end": "2026-10-19", "fees_pct": 0.0006, "holding_days": 20,
                "initial_capital": 1000000.0, "matching": "open_t+1",
                "max_exposure_pct": 1.0, "max_positions": 10, "minute_fill": False,
                "mode": "position", "overrides": {}, "params": {},
                "position_sizing": "equal", "slippage_bps": 15.0,
                "stamp_tax_pct": 0.001, "start": "2026-10-19",
                "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
            },
            "metrics": {}, "data_as_of": "2026-10-19", "status": "pending",
        },
    }
    prereg_hash = write_once(
        PREREG_PATH,
        (json.dumps(preregistration, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    print(json.dumps({
        "round": "R293", "source_files": len(source_rows), "builder_reference_hits": len(hits),
        "input_snapshot_sha256": snapshot_hash, "preregistration_sha256": prereg_hash,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
