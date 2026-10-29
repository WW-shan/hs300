#!/usr/bin/env python3
"""Freeze R274's one-shot macro-entry-gate ablation before panel registration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
CONTROL = ROOT / "data/strategies/ai/ai_hs300_macro_gate_quality.py"
CANDIDATE = ROOT / "data/strategies/ai/ai_hs300_macro_gate_quality_r274_no_macro_gate.py"
RUNNER = ROOT / "scripts/backtest_r274_no_macro_gate.py"
REGISTRAR = ROOT / "scripts/register_r274_panel.py"
OUTPUT = EVIDENCE / "r274_preregistration.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        size += path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
    return {"partition_count": len(paths), "total_size_bytes": size, "inventory_sha256": digest.hexdigest()}


def main() -> int:
    sidecar = OUTPUT.with_suffix(".sha256")
    if OUTPUT.exists() or sidecar.exists():
        raise RuntimeError("R274 preregistration exists; refusing to overwrite or tune")
    if not all(path.is_file() for path in (CONTROL, CANDIDATE, RUNNER, REGISTRAR)):
        raise RuntimeError("R274 control, candidate, runner and panel registrar must exist before freeze")
    r266_path = EVIDENCE / "r266_a_leg_validation.json"
    r266 = json.loads(r266_path.read_text(encoding="utf-8"))
    if r266.get("decision", {}).get("P1") is not True or r266.get("decision", {}).get("P2") is not True:
        raise RuntimeError("R266 finalized control validation must pass P1/P2")
    membership = ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"
    macro_root = ROOT / "data/ext_data/alt_hs300/timeseries"
    macro_config = ROOT / "data/ext_data/alt_hs300/config.json"
    panel_source = {
        "strategy_service": ROOT / "backend/app/backtest/strategy.py",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_engine": ROOT / "backend/app/strategy/engine.py",
        "indicator_pipeline": ROOT / "backend/app/indicators/pipeline.py",
        "membership_runner": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    }
    prereg = {
        "round": "R274",
        "registered_at": "2026-10-28",
        "status": "pre_registered_before_panel_registration_and_backtest",
        "classification": "single_variant_exploratory_ablation_not_independent_confirmation",
        "question": "Are R266's ERP/credit/term entry gates overly restrictive, or do they improve the A-leg's realized portfolio outcomes?",
        "hypothesis": "Removing only the three macro entry predicates increases eligible entries and may improve exposure/return, but could worsen drawdown, loss tail or concentration.",
        "control_strategy": CONTROL.stem,
        "candidate_strategy": CANDIDATE.stem,
        "only_changed_behavior": "Remove ERP >= 5.5, credit_spread_5y_low >= -0.8, term_spread_10y_2y >= 0.3 from entry filter; retain all base filters, scoring, ranking, universe, sizing, holding and execution behavior.",
        "execution": {
            "window": ["2021-06-01", "2026-10-16"],
            "discovery": ["2021-06-01", "2023-12-31"],
            "validation": ["2024-01-01", "2026-10-16"],
            "membership": "r78_daily_membership",
            "matching": "open_t+1",
            "max_positions": 10,
            "position_sizing": "equal",
            "max_hold_days": 35,
            "costs": {
                "3x": "0.0006 commission + 0.001 stamp tax + 15bp slippage",
                "4x": "proportionally scaled",
                "5x": "proportionally scaled",
            },
            "r266_control_cache_hashes": {
                cost: {kind: r266["runs"][cost][f"{kind}_cache"]["sha256"] for kind in ("curve", "trades")}
                for cost in ("3x", "4x", "5x")
            },
            "r266_evidence_sha256": _sha256(r266_path),
            "control_source_sha256": _sha256(CONTROL),
            "candidate_source_sha256": _sha256(CANDIDATE),
            "historical_membership_sha256": _sha256(membership),
            "macro_data": {"config_sha256": _sha256(macro_config), **_inventory(macro_root)},
            "system_source_sha256": {key: _sha256(path) for key, path in panel_source.items()},
            "candidate_cache_namespace": "data/research/r274_cache/",
        },
        "gates": {
            "P1": "R266 curve/trade cache hashes at 3x/4x/5x match finalized evidence.",
            "P2": "Live panel reload succeeds; R274 candidate and R266 control exist; all required stock factors exist; zero strategy load errors.",
            "G1": "3x full return positive and >=70% of R266 full return.",
            "G2": "3x maximum drawdown >= -25%.",
            "G3": "3x rolling one-year positive-return rate >=70%.",
            "G4": "3x validation return positive.",
            "G5": "3x validation annualized return >=5%.",
            "G6": "4x full return positive.",
            "G7": "5x full return positive.",
            "G8": "3x prior-close-rebased validation return retains >=70% of R266.",
            "R1": "3x Sharpe >= R266.",
            "R2": "3x MDD >= R266 MDD - 3 percentage points.",
            "L1": "3x full mean losing-trade loss magnitude improves >=1pp versus R266.",
            "L2": "3x validation-entry mean losing-trade loss magnitude improves >=1pp versus R266.",
            "L3": "3x worst full trade is less negative than R266.",
            "L4": "3x worst validation-entry trade is less negative than R266.",
            "C1": "At least 30 distinct traded symbols.",
            "C2": "Largest symbol net P&L share <=25%.",
            "C3": "Top-three symbol net P&L share <=55%.",
            "C4": "Top-five symbol net P&L share <=75%.",
            "C5": "At least 8 validation-entry symbols have positive P&L.",
            "C6": "Largest industry gross-profit share <=45%.",
            "C7": "Largest single-symbol loss <=20% of gross profit.",
            "C8": "Validation-period largest symbol net P&L share <=35%.",
        },
        "decision_rule": "Run this single frozen ablation at 3x/4x/5x only; every P/G/R/L/C gate must pass. Unknown or failed gate rejects. Do not tune thresholds or run a follow-up grid.",
        "known_limitations": [
            "R266 and the validation interval were extensively used in prior research; this is exploratory, not independent confirmation or an untouched holdout.",
            "Macro observation publication timestamps and point-in-time release lags are not independently verified.",
            "Historical HS300 membership, macro data and industry mapping may have revision/snapshot bias.",
            "Daily OHLC cannot reproduce queue priority; T+1 fills remain subject to suspension and limit-down constraints.",
            "A passing research result does not authorize paper trading or production; production remains pause_production.",
        ],
        "runner_sha256": _sha256(RUNNER),
        "panel_registrar_sha256": _sha256(REGISTRAR),
    }
    OUTPUT.write_text(json.dumps(prereg, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
    digest = _sha256(OUTPUT)
    sidecar.write_text(f"{digest}  {OUTPUT.name}{chr(10)}", encoding="ascii")
    print(json.dumps({"preregistration": OUTPUT.relative_to(ROOT).as_posix(), "sha256": digest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
