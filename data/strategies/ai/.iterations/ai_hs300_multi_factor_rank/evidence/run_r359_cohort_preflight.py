from __future__ import annotations

# ruff: noqa: E402
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNNER = EVIDENCE / "run_r359_cohort_ensemble.py"
PREREG = EVIDENCE / "r359_cohort_ensemble_preregistration.json"
OUTPUT = EVIDENCE / "r359_cohort_preflight_v14.json"
PANEL_START = date(2020, 6, 1)
START = date(2022, 5, 5)
END = date(2026, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
OFFSETS = (0, 20, 40)
STRATEGIES = {
    "A": "ai_hs300_macro_gate_quality",
    "B": "ai_hs300_industry_relative_strength_quality_h20_sl20",
    "C": "ai_hs300_quality_momentum_broad_h60",
}
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
DEFENSIVE_ASSETS = DATA / "research/r331_defensive_assets"
ASSET_FILES = ["gold_etf_qfq.parquet", "treasury_etf_qfq.parquet", "dividend_etf_qfq.parquet"]
R344_CACHE = DATA / "research/r344_cache"
R344_CACHE_INPUTS = {
    f"{strategy_id}_{start}_{factor}x_p{positions}{suffix}":
        R344_CACHE / f"{strategy_id}_{start}_{factor}x_p{positions}{suffix}"
    for strategy_id, positions in (
        (STRATEGIES["A"], 10),
        (STRATEGIES["B"], 10),
        (STRATEGIES["C"], 50),
    )
    for start, factor, suffix in (
        ("2022-05-05", 2, ".parquet"),
        ("2022-05-05", 3, ".parquet"),
        ("2022-05-05", 4, ".parquet"),
        ("2022-05-05", 5, ".parquet"),
        ("2022-05-05", 2, ".trades.json"),
        ("2022-05-05", 5, ".trades.json"),
    )
} | {
    f"{strategy_id}_{phase}_2x_p10.parquet":
        R344_CACHE / f"{strategy_id}_{phase}_2x_p10.parquet"
    for strategy_id in (STRATEGIES["A"], STRATEGIES["B"])
    for phase in ("2023-01-03", "2024-01-02", "2025-01-02", "2025-11-05", "2026-01-05")
}
ENGINE_CODE_FILES = [
    "backend/app/backtest/engine.py",
    "backend/app/backtest/strategy.py",
    "backend/app/strategy/engine.py",
    "backend/app/strategy/scoring.py",
    "backend/app/factors/store.py",
    "scripts/backtest_r112_dual_sleeve_risk_parity.py",
    "scripts/backtest_r114_b_vol_target.py",
    "scripts/backtest_r239_concentration_audit.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r331_three_asset_bucket.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r344_extended_window.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r313_concentration_audit.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r315_holding_bootstrap.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r324_defensive_ballast.py",
    "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r326_gold_concentration.py",
]
FINANCIAL_FILES = [
    DATA / "financials/metrics/part.parquet",
    DATA / "financials/income/part.parquet",
    DATA / "financials/balance_sheet/part.parquet",
    DATA / "financials/cash_flow/part.parquet",
]
FACTOR_FILES = [
    DATA / "user_data/custom_factors/uf_quality_value_defensive.json",
    DATA / "user_data/custom_factors/uf_cash_quality.json",
    DATA / "user_data/custom_factors/uf_low_vol.json",
]
INSTRUMENT_FILES = [
    DATA / "instruments/instruments.parquet",
    DATA / "financials/shares/part.parquet",
    DATA / "ext_data/ext_hy_ths/part.parquet",
]

sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import materialize_scoring_columns
from app.tickflow.repository import DataStore, KlineRepository


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path) -> dict:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    digest = hashlib.sha256()
    total_size = 0
    for item in files:
        digest.update(item.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(item).encode())
        digest.update(b"\n")
        total_size += item.stat().st_size
    return {"files": len(files), "bytes": total_size, "sha256": digest.hexdigest()}


def fingerprints() -> dict:
    return {
        "r40_enriched": tree_hash(DATA / "research/r40_engine_data/kline_daily_enriched"),
        "production_enriched": tree_hash(DATA / "kline_daily_enriched"),
        "production_raw_daily": tree_hash(DATA / "kline_daily"),
        "alt_hs300": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "financials": {path.parent.parent.name: sha256(path) for path in FINANCIAL_FILES},
        "factor_definitions": {path.name: sha256(path) for path in FACTOR_FILES},
        "instrument_and_industry_inputs": {path.relative_to(DATA).as_posix(): sha256(path) for path in INSTRUMENT_FILES},
        "membership": sha256(MEMBERSHIP),
        "industry": sha256(DATA / "ext_data/ext_hy_ths/part.parquet"),
        "defensive_assets": {name: sha256(DEFENSIVE_ASSETS / name) for name in ASSET_FILES},
        "strategies": {
            key: sha256(DATA / "strategies/ai" / f"{strategy_id}.py")
            for key, strategy_id in STRATEGIES.items()
        },
        "r344_cache_inputs": {name: sha256(path) for name, path in R344_CACHE_INPUTS.items()},
        "engine_code": {name: sha256(ROOT / name) for name in ENGINE_CODE_FILES},
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R359 preflight exists; refusing overwrite")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    before = fingerprints()
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    strategy = engine.get(STRATEGIES["C"])
    plan = StrategyDependencyResolver().resolve(
        strategy,
        params={},
        basic_filter=strategy.basic_filter,
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
        asset_type="stock",
    )
    panel = BacktestEngine(KlineRepository(DataStore(DATA))).load_panel_for_backtest(
        None, PANEL_START, END, plan, asset_type="stock"
    )
    if panel.is_empty():
        raise RuntimeError("R359 preflight loaded an empty panel")
    filter_expr = strategy.filter_fn(panel, {}) if strategy.filter_fn else pl.lit(True)
    basic_expr = StrategyEngine._basic_filter_expr(panel, strategy.basic_filter) if strategy.basic_filter else None
    candidate_expr = filter_expr if basic_expr is None else filter_expr & basic_expr
    members = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
    )
    work = panel.with_columns((candidate_expr & _hs300_panel_row_mask(panel, members)).fill_null(False).alias("_candidate"))
    work = materialize_scoring_columns(work, strategy.meta["scoring"].keys())
    daily_counts = (
        work.group_by("date")
        .agg(pl.col("_candidate").sum().alias("candidates"))
        .sort("date")
    )
    candidate_counts = {
        row["date"]: int(row["candidates"])
        for row in daily_counts.iter_rows(named=True)
    }
    counts = {str(phase): candidate_counts.get(phase, 0) for phase in PHASES}
    session_dates = (
        pl.read_parquet(
            DATA / "research/r344_cache/ai_hs300_quality_momentum_broad_h60_2022-05-05_2x_p50.parquet",
            columns=["date"],
        )["date"].cast(pl.Date).to_list()
    )
    starts = {}
    start_candidate_counts = {}
    for phase in PHASES:
        try:
            index = session_dates.index(phase)
        except ValueError as exc:
            raise RuntimeError(f"R359 preflight phase is not a known trading session: {phase}") from exc
        phase_starts = [session_dates[index + offset] for offset in OFFSETS]
        starts[str(phase)] = [str(day) for day in phase_starts]
        start_candidate_counts[str(phase)] = {str(day): candidate_counts.get(day, 0) for day in phase_starts}
    all_starts = [date.fromisoformat(value) for phase in starts.values() for value in phase]
    session_coverage = all(
        date.fromisoformat(phase_starts[-1]) < END and all(value in session_dates for value in map(date.fromisoformat, phase_starts))
        for phase_starts in starts.values()
    )
    checks = {
        "D1_strategy_and_dependencies_load": bool(
            plan.base_columns or plan.matrix_columns or plan.fundamental_columns or plan.ext_columns
        ) and panel.height > 0,
        "D2_all_18_cohort_starts_have_at_least_30_eligible_candidates": all(
            count >= 30 for phase_counts in start_candidate_counts.values() for count in phase_counts.values()
        ),
        "D3_all_phase_curves_can_reach_end": session_coverage,
        "D4_membership_snapshots_cover_all_cohort_starts": all(
            (snapshot := members.filter(pl.col("effective_date") <= day)["effective_date"].max()) is not None
            and members.filter(pl.col("effective_date") == snapshot).height >= 250
            for day in all_starts
        ),
        "D5_current_input_fingerprints_match_preregistration": before == prereg["frozen_inputs"]["data_fingerprints"],
    }
    after = fingerprints()
    checks["D6_input_fingerprints_unchanged_during_preflight"] = before == after
    payload = {
        "round": "R359",
        "status": "coverage_only_preflight_no_returns",
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "cohort_runner_sha256": sha256(RUNNER),
        "window": [str(START), str(END)],
        "panel_warmup_start": str(PANEL_START),
        "strategy": {"id": STRATEGIES["C"], "sha256": before["strategies"]["C"], "required_features": sorted(strategy.required_features)},
        "panel_rows": panel.height,
        "trading_sessions": len(session_dates),
        "candidates_on_anchor_phases": counts,
        "cohort_starts_by_phase": starts,
        "eligible_candidate_counts_at_cohort_start": start_candidate_counts,
        "data_fingerprints": before,
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_fingerprints_unchanged": before == after,
        "no_return_or_backtest_evaluated": True,
        "notes": [
            "Candidate counts use the real C filter, basic filter, factor scoring materialization and historical membership mask; no scores are ranked and no forward returns are read.",
            "Cohort offsets are trading sessions, not calendar days.",
            "The first activation phase uses fixed one-third initial capital and pre-activation cash; no interest is imputed.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"all_checks": payload["all_checks"], "checks": checks, "candidate_counts": start_candidate_counts, "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
