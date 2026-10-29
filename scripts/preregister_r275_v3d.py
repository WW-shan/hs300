#!/usr/bin/env python3
"""Freeze the three single-macro-gate ablations before panel reload or backtests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
AI = ROOT / "data/strategies/ai"
CONTROL = AI / "ai_hs300_macro_gate_quality.py"
CANDIDATES = {
    "erp": AI / "ai_hs300_macro_gate_quality_r275_remove_erp.py",
    "credit": AI / "ai_hs300_macro_gate_quality_r275_remove_credit.py",
    "term": AI / "ai_hs300_macro_gate_quality_r275_remove_term.py",
}
RUNNER = ROOT / "scripts/backtest_r275_single_gate_ablation_v3d.py"
REGISTRAR = ROOT / "scripts/register_r275_panel_v3d.py"
OUT = EVIDENCE / "r275_preregistration_v3d.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\n")
        size += path.stat().st_size
    return {"partition_count": len(paths), "total_size_bytes": size, "inventory_sha256": digest.hexdigest()}


def main() -> int:
    sidecar = OUT.with_suffix(".sha256")
    if OUT.exists() or sidecar.exists():
        raise RuntimeError("R275 preregistration exists; refusing overwrite or retuning")
    required = [CONTROL, RUNNER, REGISTRAR, *CANDIDATES.values()]
    if not all(path.is_file() for path in required):
        raise RuntimeError("R275 control, all candidates, runner and panel registrar must exist before freeze")
    r266_path = EVIDENCE / "r266_a_leg_validation.json"
    r266 = json.loads(r266_path.read_text(encoding="utf-8"))
    if not all(r266.get("decision", {}).get(key) is True for key in ("P1", "P2")):
        raise RuntimeError("finalized R266 control must pass P1/P2")
    source_paths = {
        "strategy_service": ROOT / "backend/app/backtest/strategy.py",
        "backtest_engine": ROOT / "backend/app/backtest/engine.py",
        "strategy_engine": ROOT / "backend/app/strategy/engine.py",
        "indicator_pipeline": ROOT / "backend/app/indicators/pipeline.py",
        "membership_runner": ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py",
        "concentration_audit": ROOT / "scripts/backtest_r239_concentration_audit.py",
    }
    costs = ("3x", "4x", "5x")
    payload = {
        "round": "R275",
        "registered_at": "2026-10-28",
        "status": "frozen_before_live_panel_reload_and_outcome_backtests",
        "classification": "three_candidate_single_gate_family_exploratory_not_independent_confirmation",
        "question": "Which individual R266 macro entry gate contributes useful selection or risk control, and which is unnecessarily restrictive?",
        "selection_basis": {
            "prior_result": "R274 removed all three gates and improved returns while failing drawdown, loss-tail and top-3/top-5 concentration gates",
            "prior_result_sha256": _sha256(EVIDENCE / "r274_no_macro_gate.json"),
            "candidate_family": "remove exactly one of ERP, credit spread, or term spread; retain the other two at R266 thresholds",
            "no_threshold_or_score_search": True,
        },
        "control_strategy": CONTROL.stem,
        "candidates": {key: {"strategy_id": path.stem, "source_sha256": _sha256(path)} for key, path in CANDIDATES.items()},
        "only_changed_behavior": {
            "erp": "Remove ERP >= 5.5; retain credit_spread_5y_low >= -0.8 and term_spread_10y_2y >= 0.3.",
            "credit": "Remove credit_spread_5y_low >= -0.8; retain ERP >= 5.5 and term_spread_10y_2y >= 0.3.",
            "term": "Remove term_spread_10y_2y >= 0.3; retain ERP >= 5.5 and credit_spread_5y_low >= -0.8.",
        },
        "execution": {
            "window": ["2021-06-01", "2026-10-16"],
            "discovery": ["2021-06-01", "2023-12-31"],
            "validation": ["2024-01-01", "2026-10-16"],
            "membership": "r78_daily_membership",
            "matching": "open_t+1",
            "max_positions": 10,
            "position_sizing": "equal",
            "max_hold_days": 35,
            "costs": {"3x": "0.0006 commission + 0.001 stamp tax + 15bp slippage", "4x": "proportionally scaled", "5x": "proportionally scaled"},
            "r266_evidence_sha256": _sha256(r266_path),
            "r266_control_cache_hashes": {cost: {kind: r266["runs"][cost][f"{kind}_cache"]["sha256"] for kind in ("curve", "trades")} for cost in costs},
            "control_source_sha256": _sha256(CONTROL),
            "historical_membership_sha256": _sha256(ROOT / "data/research/r78_daily_membership/daily_snapshots.parquet"),
            "macro_data": {"config_sha256": _sha256(ROOT / "data/ext_data/alt_hs300/config.json"), **_inventory(ROOT / "data/ext_data/alt_hs300/timeseries")},
            "system_source_sha256": {name: _sha256(path) for name, path in source_paths.items()},
            "cache_namespace": "data/research/r275_cache_v3d/",
        },
        "gates_per_candidate": {
            "P1": "R266 control evidence and 3x/4x/5x curve/trade cache hashes match finalized evidence.",
            "P2": "Live reload succeeds; control and all three research candidates and four stock factors are registered; zero strategy load errors; synthetic truth table proves exactly one predicate was removed per candidate.",
            "G1": "3x full return positive and >=70% of R266 full return.",
            "G2": "3x maximum drawdown >= -25%.",
            "G3": "3x rolling one-year positive-return rate >=70%.",
            "G4": "3x validation return positive and annualized return >=5%.",
            "G5": "3x prior-close-rebased validation return retains >=70% of R266.",
            "G6": "4x and 5x full returns are both positive.",
            "R1": "3x Sharpe >= R266.",
            "R2": "3x maximum drawdown no worse than R266 by more than 3 percentage points.",
            "L1": "3x mean losing-trade loss magnitude no more than R266 +1 percentage point.",
            "L2": "3x worst trade return no worse than R266 by more than 5 percentage points.",
            "C1": "At least 30 distinct traded symbols.",
            "C2": "Largest symbol net P&L share <=25%.",
            "C3": "Top-three symbol net P&L share <=55%.",
            "C4": "Top-five symbol net P&L share <=75%.",
            "C5": "At least 8 validation-entry symbols have positive P&L.",
            "C6": "Largest industry gross-profit share <=45%.",
            "C7": "Largest single-symbol loss <=20% of gross profit.",
            "C8": "Validation-period largest symbol net P&L share <=35%.",
        },
        "family_decision_rule": "Run all three preregistered variants; do not select, retune or promote any variant unless that variant passes every listed gate. Results are exploratory because the family follows prior outcome-informed research; any apparent winner requires a new untouched prospective/time holdout. Production remains pause_production.",
        "known_limitations": [
            "R266/R274 and the historical validation segment have been repeatedly examined; this family is not independent confirmation.",
            "Macro publication timestamps and point-in-time release lags remain unverified.",
            "Historical HS300 membership and macro snapshots may contain revision or timing bias.",
            "Daily OHLC cannot reproduce queue priority; system T+1 tradability and limit constraints remain approximate.",
            "The three candidates form one outcome-informed family; choosing a passing member would require a fresh prospective holdout.",
        ],
        "runner_sha256": _sha256(RUNNER),
        "panel_registrar_sha256": _sha256(REGISTRAR),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = _sha256(OUT)
    sidecar.write_text(f"{digest}  {OUT.name}\n", encoding="ascii")
    print(json.dumps({"preregistration": OUT.relative_to(ROOT).as_posix(), "sha256": digest, "candidates": {key: value["strategy_id"] for key, value in payload["candidates"].items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
