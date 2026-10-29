#!/usr/bin/env python3
"""Freeze the R273 single-variant gate-exit test before registration or backtest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
RUNNER = ROOT / "scripts/backtest_r273_gate_exit.py"
REGISTRAR = ROOT / "scripts/register_r273_panel.py"
STRATEGY = ROOT / "data/strategies/ai/ai_hs300_macro_gate_quality_r273_gate_exit.py"
PREVIOUS_PREREG = EVIDENCE / "r273_preregistration.json"
PREVIOUS_PREREG_HASH = EVIDENCE / "r273_preregistration.sha256"
SECOND_PREREG = EVIDENCE / "r273_preregistration_v2.json"
SECOND_PREREG_HASH = SECOND_PREREG.with_suffix(".sha256")
FAILED_PANEL_PROOF = EVIDENCE / "r273_panel_registration.json"
OUTPUT = EVIDENCE / "r273_preregistration_v4.json"
SIGNAL_FILES = [
    ROOT / "data/user_data/custom_signals/r273_macro_exit_erp.json",
    ROOT / "data/user_data/custom_signals/r273_macro_exit_credit.json",
    ROOT / "data/user_data/custom_signals/r273_macro_exit_term.json",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        content_hash = _sha256(path)
        size += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(paths), "total_size_bytes": size, "inventory_sha256": digest.hexdigest()}


def main() -> int:
    output = OUTPUT
    sidecar = output.with_suffix(".sha256")
    if output.exists() or sidecar.exists():
        raise RuntimeError("R273 preregistration exists; refusing to overwrite or tune")
    if not PREVIOUS_PREREG.exists() or not PREVIOUS_PREREG_HASH.exists() or not SECOND_PREREG.exists() or not SECOND_PREREG_HASH.exists() or not FAILED_PANEL_PROOF.exists():
        raise RuntimeError("R273 setup-correction record is incomplete")
    previous_digest = _sha256(PREVIOUS_PREREG)
    if PREVIOUS_PREREG_HASH.read_text(encoding="ascii").split()[0] != previous_digest:
        raise RuntimeError("initial R273 preregistration checksum mismatch")
    second_digest = _sha256(SECOND_PREREG)
    if SECOND_PREREG_HASH.read_text(encoding="ascii").split()[0] != second_digest:
        raise RuntimeError("second R273 preregistration checksum mismatch")
    failed_panel = json.loads(FAILED_PANEL_PROOF.read_text(encoding="utf-8"))
    if failed_panel.get("backtest_run") is not False or failed_panel.get("all_checks_pass") is not False:
        raise RuntimeError("initial setup evidence does not show a pre-backtest registration-check failure")
    failed_checks = failed_panel.get("checks", {})
    if not all(failed_checks.get(key) is False for key in (
        "candidate_and_control_research_only", "candidate_exit_signals_match", "source_contracts_match"
    )):
        raise RuntimeError("initial panel proof failure is not the known registration-check contract issue")
    controls = json.loads((EVIDENCE / "r266_a_leg_validation.json").read_text(encoding="utf-8"))
    macro_root = ROOT / "data/ext_data/alt_hs300/timeseries"
    membership = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    macro_config = ROOT / "data/ext_data/alt_hs300/config.json"
    system_sources = {
        "strategy_service": ROOT / "backend/app/backtest/strategy.py",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_engine": ROOT / "backend/app/strategy/engine.py",
        "custom_signals": ROOT / "backend/app/strategy/custom_signals.py",
        "indicator_pipeline": ROOT / "backend/app/indicators/pipeline.py",
        "historical_membership_source": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    }
    payload = {
        "round": "R273",
        "registered_at": "2026-10-28",
        "status": "pre_registered_after_setup_evidence_correction_before_backtest",
        "setup_correction": {
            "supersedes_preregistrations": [
                {"path": PREVIOUS_PREREG.relative_to(ROOT).as_posix(), "sha256": previous_digest},
                {"path": SECOND_PREREG.relative_to(ROOT).as_posix(), "sha256": second_digest},
            ],
            "failed_setup_evidence": {"path": FAILED_PANEL_PROOF.relative_to(ROOT).as_posix(), "sha256": _sha256(FAILED_PANEL_PROOF)},
            "cause": "The first proof check wrongly required the existing public R266 control to be research-only and omitted the csg_ signal-column prefix; registration itself succeeded, but the proof failed. Before retrying, the evidence-overwrite guard stopped the corrected registrar from rerunning; a separate v2 proof file will capture the corrected registration attempt. No backtest was run and no result was read before this freeze.",
            "hypothesis_thresholds_data_window_and_candidate_source_unchanged": True,
        },
        "classification": "single_variant_exploratory_test_not_independent_confirmation",
        "hypothesis": (
            "R266's three macro gates constrain new entries but do not immediately exit existing "
            "positions. Keep R266 entry, ranking, universe, sizing and max-hold unchanged; exit "
            "positions when any gate is closed, using the system's next-session open. This may "
            "reduce risk exposure but can sell before a rapid reopening/rebound."
        ),
        "strategy": "ai_hs300_macro_gate_quality_r273_gate_exit",
        "control_round": "R266",
        "execution": {
            "window": ["2021-06-01", "2026-10-16"],
            "discovery": ["2021-06-01", "2023-12-31"],
            "validation": ["2024-01-01", "2026-10-16"],
            "membership": "r78_daily_membership",
            "matching": "entry and exit open_t+1",
            "costs": {
                "3x": "0.0006 commission + 0.001 stamp tax + 15bp slippage",
                "4x": "proportionally scaled",
                "5x": "proportionally scaled",
            },
            "max_positions": 10,
            "position_sizing": "equal",
            "max_hold_days": 35,
            "gate_thresholds": {
                "erp_min": 5.5,
                "credit_spread_5y_low_min": -0.8,
                "term_spread_10y_2y_min": 0.3,
            },
            "exit_logic": "OR of ERP < 5.5, credit spread factor < -0.8, or term spread < 0.3; next tradable session open fill.",
            "only_changed_behavior": "add T+1 exits on any closed original entry gate; entry filter/ranking remain unchanged",
            "candidate_cache_namespace": "data/research/r273_cache/",
            "strategy_source_sha256": _sha256(STRATEGY),
            "signal_sources": {p.relative_to(ROOT).as_posix(): _sha256(p) for p in SIGNAL_FILES},
            "control_cache_hashes": {
                cost: {
                    kind: controls["runs"][cost][f"{kind}_cache"]["sha256"]
                    for kind in ("curve", "trades")
                }
                for cost in ("3x", "4x", "5x")
            },
            "control_evidence_sha256": _sha256(EVIDENCE / "r266_a_leg_validation.json"),
            "control_strategy_sha256": _sha256(ROOT / "data/strategies/ai/ai_hs300_macro_gate_quality.py"),
            "historical_membership_sha256": _sha256(membership),
            "macro_data": {
                "config_sha256": _sha256(macro_config),
                **_inventory(macro_root),
            },
            "system_source_sha256": {key: _sha256(path) for key, path in system_sources.items()},
            "system_path": "custom signal registry -> StrategyDependencyResolver -> StrategyBacktestService -> BacktestEngine -> T+1 signal exit",
        },
        "gates": {
            "P1": "R266 control curve/trade cache hashes at 3x/4x/5x match finalized evidence and 3x R265 parity.",
            "P2": "Live panel reload succeeds; candidate/control and all 3 exit signals/4 factors are registered; zero strategy load errors.",
            "G1": "3x full return is positive and retains >=70% of R266 full return.",
            "G2": "3x maximum drawdown >= -25%.",
            "G3": "3x rolling one-year positive-return rate >=70%.",
            "G4": "3x validation return >0.",
            "G5": "3x validation annualized return >=5%.",
            "G6": "4x full return >0.",
            "G7": "5x full return >0.",
            "G8": "3x validation return retains >=70% of R266 validation return using prior-close-rebased basis.",
            "R1": "3x Sharpe >= R266 control.",
            "R2": "3x MDD >= R266 MDD - 3 percentage points.",
            "L1": "3x full mean losing-trade loss magnitude improves >=1pp versus R266.",
            "L2": "3x validation-entry mean losing-trade loss magnitude improves >=1pp versus R266.",
            "L3": "3x worst full completed trade is less negative than R266.",
            "L4": "3x worst validation-entry completed trade is less negative than R266.",
            "C1": "At least 30 distinct traded symbols.",
            "C2": "Largest symbol net P&L share <=25%.",
            "C3": "Top three symbols net P&L share <=55%.",
            "C4": "Top five symbols net P&L share <=75%.",
            "C5": "At least 8 validation-entry symbols have positive P&L.",
            "C6": "Largest industry gross-profit share <=45%.",
            "C7": "Largest single-symbol loss <=20% of gross profit.",
            "C8": "Validation-period largest symbol net P&L share <=35%.",
        },
        "decision_rule": "Only this frozen candidate is run; every P/G/R/L/C gate must pass. Any failed or unknown gate rejects it. No threshold changes or follow-up grid in this round.",
        "known_limitations": [
            "R266 and its validation interval have been used by extensive prior research; this is exploratory, not independent confirmation or an untouched holdout.",
            "Macro time series are aligned to stored dates; publication timestamps and PIT release lags are not independently established here.",
            "Historical HS300 membership and macro-source data can have revision risk.",
            "Daily OHLC cannot reproduce queue priority; T+1 exits remain blocked by suspension or limit-down execution constraints.",
            "Exit signals use the stored daily gate state; positions can remain exposed until the next tradable open.",
            "A passing research test does not authorize paper trading or production; production remains pause_production.",
        ],
        "runner_sha256": _sha256(RUNNER),
        "panel_registrar_sha256": _sha256(REGISTRAR),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + chr(10)
    output.write_text(raw, encoding="utf-8")
    digest = _sha256(output)
    sidecar.write_text(f"{digest}  {output.name}{chr(10)}", encoding="ascii")
    print(json.dumps({"preregistration": output.relative_to(ROOT).as_posix(), "sha256": digest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
