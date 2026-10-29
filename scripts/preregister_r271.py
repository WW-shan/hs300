#!/usr/bin/env python3
"""Freeze the R271 hypothesis, candidate and gates before running its backtest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data" / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
RUNNER = ROOT / "scripts" / "backtest_r271_shrink_neutralization.py"
REGISTRAR = ROOT / "scripts" / "register_r271_panel.py"
CANDIDATE_STRATEGY = ROOT / "data" / "strategies" / "ai" / "ai_hs300_macro_gate_quality_r271_industry_shrink05.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    payload = {
        "round": "R271",
        "registered_at": "2026-10-28",
        "status": "pre_registered_before_backtest",
        "classification": "exploratory_not_independent_confirmation",
        "hypothesis": (
            "A fixed 0.5 shrink of the system's industry-neutral scoring transform may reduce "
            "industry-linked concentration while preserving more of the default A-leg score "
            "ordering than full shrink=1.0. This is a single exploratory contrast against R266; "
            "prior R98 industry-neutralization results and this strategy's extensive history are "
            "known, so neither discovery nor validation is an untouched holdout."
        ),
        "strategy": "ai_hs300_macro_gate_quality",
        "control_round": "R266",
        "execution": {
            "window": ["2021-06-01", "2026-10-16"],
            "discovery": ["2021-06-01", "2023-12-31"],
            "validation": ["2024-01-01", "2026-10-16"],
            "membership": "r78_daily_membership",
            "matching": "open_t+1",
            "costs": ["3x primary", "4x stress", "5x stress"],
            "max_positions": 10,
            "position_sizing": "equal",
            "unchanged": [
                "strategy signals", "filters", "scoring weights", "35-day max hold",
                "universe", "entry/exit matching", "fees", "slippage", "all gates"
            ],
            "only_changed_parameter": "system neutralize override: industry_l1 with shrink=0.5",
            "variants": {
                "control": {},
                "industry_shrink_05": {
                    "neutralize": {"industry": "industry_l1", "shrink": 0.5}
                },
            },
            "candidate_cache_namespace": "data/research/r271_cache/",
            "candidate_strategy": "ai_hs300_macro_gate_quality_r271_industry_shrink05",
            "candidate_strategy_source_sha256": _sha256(CANDIDATE_STRATEGY),
            "implementation": (
                "StrategyBacktestService -> BacktestEngine -> existing "
                "scoring.neutralize_scoring_columns; candidate transforms are calculated "
                "inside each date's HS300 candidate universe."
            ),
            "industry_data": "current industry_l1 snapshot; not point-in-time",
            "control": "R266 finalized curve/trade caches; byte hashes checked before comparison",
        },
        "gates": {
            "P1": "R266 equal-weight 3x/4x/5x curve and trade hashes match finalized R266 evidence; candidate caches have complete manifests and fresh system runs.",
            "P2": "After running-panel reload, base strategy and its four referenced factors exist with zero strategy load errors.",
            "G1": "3x full-sample return is positive and retains at least 70% of R266 equal-weight return.",
            "G2": "3x maximum drawdown is no worse than -25%.",
            "G3": "3x rolling one-year positive-return rate is at least 70%.",
            "G4": "3x validation-period return is positive.",
            "G5": "3x validation annualized return is at least 5%.",
            "G6": "4x full-sample return is positive.",
            "G7": "5x full-sample return is positive.",
            "G8": "3x validation return retains at least 70% of R266 validation return using the same prior-close-rebased calculation.",
            "R1": "3x full-sample Sharpe is no lower than R266 equal-weight control.",
            "R2": "3x maximum drawdown is no worse than R266 control by more than 3 percentage points.",
            "L1": "3x full-sample mean loss magnitude is at least 1 percentage point smaller than R266.",
            "L2": "3x validation-entry mean loss magnitude is at least 1 percentage point smaller than R266.",
            "L3": "3x full-sample worst completed trade is less negative than R266.",
            "L4": "3x validation-entry worst completed trade is less negative than R266.",
            "C1": "At least 30 distinct symbols traded.",
            "C2": "Largest symbol net P&L share is at most 25%.",
            "C3": "Top three symbols net P&L share is at most 55%.",
            "C4": "Top five symbols net P&L share is at most 75%.",
            "C5": "At least 8 distinct validation-entry symbols have positive P&L.",
            "C6": "Largest industry gross-profit share is at most 45%.",
            "C7": "Largest single-symbol loss is at most 20% of gross profit.",
            "C8": "Validation-period largest symbol net P&L share is at most 35%.",
        },
        "decision_rule": (
            "The single shrink=0.5 candidate is rejected if any P/G/R/L/C gate fails. No threshold "
            "or variant changes after result exposure. Passing only allows separate research "
            "discussion; paper trading and production are not authorized by this round."
        ),
        "diagnostics_not_gates": [
            "Report scoring-variant trade count and all 3x/4x/5x curves/trades with SHA-256.",
            "Report discovery/validation and rolling return statistics, top symbol and industry concentration, trade-loss distribution, and cost stress.",
            "Compare full neutralization shrink=1.0 only as already-known R98 context; do not rerun it or tune shrink based on this result.",
        ],
        "known_limitations": [
            "R98 previously tested full industry neutralization and industry plus size neutralization; those older results used pre-R125 holder data and 2x costs. R271 is an exploratory updated-score test, not a fresh confirmatory experiment.",
            "R270's initial results had already been exposed before its final byte-identical recalculation; that audit does not restore independence.",
            "R271 reuses a strategy and validation interval examined across many prior rounds; validation is not an untouched holdout.",
            "Industry classification is a current snapshot, not point-in-time historical industry membership.",
            "Score neutralization is not portfolio-level industry neutrality and does not enforce industry quotas.",
            "Macro factor source data may be revised; historical HS300 membership is used.",
            "Only 290 R266 baseline trades are available; concentration and tail estimates have sampling uncertainty.",
            "Panel reload and passing historical backtest do not authorize paper or production deployment.",
        ],
        "runner_sha256": _sha256(RUNNER),
        "panel_registrar_sha256": _sha256(REGISTRAR),
        "preregistration_tool_sha256": _sha256(Path(__file__).resolve()),
    }
    path = EVIDENCE / "r271_preregistration.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(path)
    path.with_suffix(".sha256").write_text(
        digest + "  " + path.name + "\n", encoding="ascii"
    )
    print(json.dumps({"preregistration": path.relative_to(ROOT).as_posix(), "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
