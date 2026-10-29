#!/usr/bin/env python3
"""Freeze R295's post-hoc concentration-metric audit inputs and rules."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
EVIDENCE = Path(__file__).resolve().parent
ROUND = "R295"
SNAPSHOT = EVIDENCE / "r295_input_snapshot.json"
PREREG = EVIDENCE / "r295_preregistration.json"

TRADE_FILES = {
    "candidate_discovery": "data/research/r257_cache/ai_hs300_quality_relative_strength_h20_sl20_3x_2023-05-04_2024-12-31.trades.json",
    "candidate_validation": "data/research/r257_cache/ai_hs300_quality_relative_strength_h20_sl20_3x_2025-01-02_2026-10-16.trades.json",
    "control_discovery": "data/research/r257_cache/ai_hs300_quality_relative_strength_control_3x_2023-05-04_2024-12-31.trades.json",
    "control_validation": "data/research/r257_cache/ai_hs300_quality_relative_strength_control_3x_2025-01-02_2026-10-16.trades.json",
}
SOURCE_FILES = [
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_preregistration.json",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/r257_quality_relative_strength.json",
    "scripts/backtest_r257_quality_relative_strength.py",
    "scripts/backtest_r239_concentration_audit.py",
    *TRADE_FILES.values(),
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_once(path: Path, payload: bytes) -> str:
    with path.open("xb") as stream:
        stream.write(payload)
    digest = sha256_bytes(payload)
    sidecar = path.with_name(path.name + ".sha256")
    with sidecar.open("x", encoding="ascii") as stream:
        stream.write(f"{digest}  {path.name}\n")
    return digest


def main() -> int:
    targets = (SNAPSHOT, PREREG)
    if any(path.exists() or path.with_name(path.name + ".sha256").exists() for path in targets):
        raise RuntimeError("R295 freeze artifacts already exist; refusing to overwrite")

    files = []
    for relative in SOURCE_FILES:
        path = ROOT / relative
        stat = path.stat()
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            }
        )

    snapshot = {
        "round": ROUND,
        "classification": "posthoc_method_audit_frozen_input_manifest_not_alpha_confirmation",
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "prior_artifacts_and_outcomes_inspected": True,
        "files": files,
    }
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    snapshot_hash = write_once(SNAPSHOT, snapshot_bytes)

    candidate = {
        "kind": "strategy",
        "name": "R295 R257集中度门槛口径与头部归因审计",
        "source_id": "ai_hs300_quality_relative_strength_h20_sl20",
        "config": {
            "asset_type": "stock",
            "commission_pct": 0.0006,
            "end": "2026-10-16",
            "fees_pct": 0.0006,
            "holding_days": 20,
            "initial_capital": 1000000.0,
            "matching": "open_t+1",
            "max_exposure_pct": 1.0,
            "max_positions": 10,
            "minute_fill": False,
            "mode": "position",
            "overrides": {},
            "params": {},
            "position_sizing": "equal",
            "slippage_bps": 15.0,
            "stamp_tax_pct": 0.001,
            "start": "2023-05-04",
            "strategy_id": "ai_hs300_quality_relative_strength_h20_sl20",
        },
        "metrics": {},
        "data_as_of": "2026-10-16",
        "status": "pending",
    }
    prereg = {
        "round": ROUND,
        "status": "frozen_before_r295_recalculation",
        "classification": "posthoc_method_audit_not_independent_alpha_confirmation",
        "frozen_at_utc": datetime.now(UTC).isoformat(),
        "research_question": (
            "Do R257's C2-C4 thresholds measure dependence on a small number of winners, or are their pass/fail results materially driven by using total net P&L as the denominator?"
        ),
        "hypotheses": [
            "A positive-contributor denominator yields bounded shares that sum to 100%, unlike top-positive-symbol P&L divided by portfolio net P&L.",
            "A change in C2-C4 classification under an alternate denominator demonstrates metric sensitivity, not that either threshold is universally correct.",
            "Static top-contributor removal from archived trade P&L is attribution only and cannot reproduce a path-dependent portfolio counterfactual.",
        ],
        "prior_knowledge_disclosure": (
            "R257 preregistration, result JSON, gate implementation, and initial summary metrics had already been inspected before this round. This is explicitly a post-hoc methodological audit; the preregistration does not make the historical outcomes blind or independent. The freeze script only fingerprints the archived source files and does not parse trade outcomes."
        ),
        "frozen_inputs": {
            "manifest": SNAPSHOT.name,
            "manifest_sha256": snapshot_hash,
            "trade_files_by_role": TRADE_FILES,
            "source_files_sha256": {row["path"]: row["sha256"] for row in files},
        },
        "method": [
            "Read only the four frozen 3x system-backtest trade logs: R257 candidate/control in the preregistered discovery and validation segments.",
            "Re-aggregate each trade's stored pnl_amount by symbol; separately report total net trade P&L, gross positive trade P&L, gross losses, and the sum of positive symbol-level net P&L.",
            "For top 1/3/5 positive-contributor symbols, calculate legacy share = top-symbol net contribution / total portfolio net P&L; alternate share = top-symbol net contribution / sum of positive symbol-level net P&L; and gross-profit share = top-symbol net contribution / gross positive trade P&L.",
            "Report static attribution after subtracting the top 1/3/5 positive symbol-level contributors from segment trade P&L. Do not call it a rerun, counterfactual equity curve, or executable strategy result.",
            "Reconcile candidate full-sample and validation totals and legacy shares against the frozen R257 result JSON.",
        ],
        "precommitted_checks": {
            "A1": "all four trade files match frozen size, mtime_ns, and SHA-256 before and after audit",
            "A2": "candidate validation/full totals and legacy top1/top3/top5 shares reproduce the frozen R257 concentration artifact at its stored precision",
            "A3": "alternate denominator is positive when shares are reported, all alternate shares lie in [0,1], and top1 <= top3 <= top5",
            "A4": "candidate and matched control are reported for both frozen segments and the combined additive trade-P&L attribution",
            "A5": "any denominator-dependent gate classification is labelled metric-sensitive; it does not revise the original frozen R257 decision",
        },
        "decision_rules": [
            "If A1-A4 pass, conclude only that the arithmetic audit is reproducible.",
            "If alternate C2-C4 differ from the frozen verdict, record the original gates as denominator-sensitive; do not relax, replace, or retroactively pass them.",
            "Any data mismatch, missing trade log, non-positive denominator where a share is required, or failed reconciliation makes the audit incomplete and must not be repaired by changing the frozen files.",
            "R295 cannot establish or reject alpha because the sample and outcomes were previously inspected; no production or paper-trading promotion follows.",
        ],
        "fixed_context": {
            "strategy": "ai_hs300_quality_relative_strength_h20_sl20",
            "matched_control": "ai_hs300_quality_relative_strength_control",
            "matching": "open_t+1",
            "cost": "R257 3x: 6bp fees + 6bp commission + 15bp slippage + 10bp stamp tax",
            "discovery": ["2023-05-04", "2024-12-31"],
            "validation": ["2025-01-02", "2026-10-16"],
            "full_sample": "additive concatenation of the two independently initialized trade sets; not a compounded portfolio return",
        },
        "panel_candidate": candidate,
        "scope_exclusions": [
            "No provider, builder, strategy engine, backtest service, screener, or signal generator is called in R295.",
            "No market, extension-factor, strategy, cache, paper account, or production data is written or changed.",
            "No new alpha, independent sample, benchmark excess return, or deployable portfolio conclusion is tested.",
        ],
        "freeze_script_sha256": sha256_file(Path(__file__)),
    }
    prereg_bytes = (json.dumps(prereg, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    prereg_hash = write_once(PREREG, prereg_bytes)
    print(json.dumps({"round": ROUND, "snapshot_sha256": snapshot_hash, "preregistration_sha256": prereg_hash, "frozen_files": len(files)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
