#!/usr/bin/env python3
"""Freeze R272's data-only feasibility checks before inspecting factor coverage."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence"
PROBE = ROOT / "scripts/probe_r272_operating_margin_improvement.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    payload = {
        "round": "R272-feasibility",
        "registered_at": "2026-10-28",
        "status": "frozen_before_factor_coverage_correlation_or_forward_return_analysis",
        "hypothesis": (
            "A year-over-year change in single-quarter operating-profit margin may capture"
            " operating leverage beyond gross-margin change, SG&A level, and cash conversion."
        ),
        "factor_definition": {
            "column": "operating_margin_yoy_change_q",
            "formula": "(quarterly operating_profit / quarterly revenue)_t - (quarterly operating_profit / quarterly revenue)_(t-4)",
            "quarterization": "Q1 uses reported cumulative values; Q2-Q4 use the difference from the immediately preceding quarter only when period indices are consecutive",
            "point_in_time": "For every source period use financial_pit_date's statutory-deadline fallback; each quarterized margin is available only after both cumulative reports it depends on, and the YoY change only after both year-separated margins are available. Carry no more than 180 calendar days.",
            "universe": "Historical daily HS300 members excluding banks and non-bank financials using the current industry snapshot; this is a current-industry classification, not historical PIT.",
            "evaluation_window": ["2023-05-04", "2026-10-16"],
        },
        "gates": {
            "F1": "Daily historical non-financial HS300 member-row coverage from 2023-05-04 through 2026-10-16 is at least 99%.",
            "F2": "Coverage over the last 252 available trading dates is at least 99%.",
            "F3": "First usable value is available no later than 2023-05-04.",
            "F4": "At least 24 month-end cross-sections are usable; maximum mean absolute cross-sectional Spearman correlation versus the frozen existing-factor list is no more than 0.70.",
            "F5": "The factor is finite and non-constant on at least 95% of evaluated dates with at least 30 eligible members.",
        },
        "comparison_factors": [
            "ocf_margin_4q",
            "cash_to_assets",
            "sga_ratio_4q_low",
            "gross_margin_yoy_improvement_q",
            "gross_margin_stability_4q_low",
            "risk_adjusted_momentum_60d",
            "trend_efficiency_60d",
            "idio_vol_60d_low",
            "stock_rel_industry_20d",
            "capex_intensity_low",
            "free_cash_flow_assets",
            "asset_growth_yoy_low",
            "working_capital_change_yoy_low",
            "gross_profitability",
            "piotroski7_score",
            "management_ocf_gm_min_rank",
            "quality_relative_strength_min_rank",
        ],
        "decision_rule": (
            "Only if F1-F5 all pass will a separate strategy/backtest preregistration be written."
            " Any feasibility failure closes this factor before examining any forward return."
        ),
        "prohibitions": [
            "Do not load forward OHLCV/returns, trade caches, equity curves, or candidate backtest results.",
            "Do not change the formula, age limit, comparison list, or gates after inspecting the result.",
            "Passing data feasibility is not evidence of alpha and does not authorize panel promotion, paper trading, or production.",
        ],
        "known_limitations": [
            "Local financial tables are current snapshots; statutory-date fallback reduces use of suspicious announcement dates but cannot remove restatement/snapshot leakage.",
            "Current industry classifications are used to exclude financials.",
        ],
        "probe_sha256": sha256(PROBE),
        "preregistration_script_sha256": sha256(Path(__file__).resolve()),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE / "r272_feasibility_preregistration.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = sha256(path)
    path.with_suffix(".sha256").write_text(digest + "  " + path.name + "\n", encoding="ascii")
    print(json.dumps({"path": path.relative_to(ROOT).as_posix(), "sha256": digest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
