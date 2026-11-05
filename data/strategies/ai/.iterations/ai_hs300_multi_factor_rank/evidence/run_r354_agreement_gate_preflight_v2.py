from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import polars as pl

# ruff: noqa: E402

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r354_factor_agreement_gate_preflight_v2.json"
FACTOR_FILE = DATA / "user_data/custom_factors/uf_factor_agreement.json"
CANDIDATE_STRATEGY = "ai_hs300_quality_momentum_agreement_gate_h60"
CONTROL_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
PANEL_START = date(2020, 6, 1)
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"

sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
from app.factors.dsl import compile_formula
from app.factors.store import load_into_registry
from app.services.industry_map import attach_industry_l1, industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.strategy.scoring import materialize_scoring_columns
from app.tickflow.repository import DataStore, KlineRepository


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root: Path) -> dict:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("part.parquet"))
    total = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(path).encode())
        digest.update(b"\n")
        total += path.stat().st_size
    return {"files": len(paths), "bytes": total, "sha256": digest.hexdigest()}


def input_hashes() -> dict:
    return {
        "kline_daily_enriched": tree_hash(DATA / "kline_daily_enriched"),
        "alt_hs300": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "financials": {
            name: sha256(DATA / "financials" / name / "part.parquet")
            for name in ("metrics", "income", "balance_sheet", "cash_flow")
        },
        "membership": sha256(MEMBERSHIP),
        "candidate_strategy": sha256(DATA / "strategies/ai" / f"{CANDIDATE_STRATEGY}.py"),
        "control_strategy": sha256(DATA / "strategies/ai" / f"{CONTROL_STRATEGY}.py"),
        "factor_definition": sha256(FACTOR_FILE),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R354 v2 preflight exists; refusing overwrite")

    before = input_hashes()
    factor = json.loads(FACTOR_FILE.read_text(encoding="utf-8"))
    compiled = compile_formula(factor["formula"])
    if not compiled.ok:
        raise RuntimeError(f"R354 factor formula invalid: {compiled.errors}")
    expected_dependencies = {"momentum_60d", "roe_latest", "pb_latest", "debt_ratio_latest", "ocf_ni", "accrual", "annual_vol_20d"}
    if compiled.dependencies != expected_dependencies:
        raise RuntimeError(f"R354 factor dependencies changed: {sorted(compiled.dependencies)}")

    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    strategy = engine.get(CANDIDATE_STRATEGY)
    control = engine.get(CONTROL_STRATEGY)
    expected_scoring = {
        "momentum_60d": 0.40,
        "uf_quality_value_defensive": 0.25,
        "uf_cash_quality": 0.20,
        "uf_low_vol": 0.15,
    }
    if strategy is None or strategy.meta.get("scoring") != expected_scoring:
        raise RuntimeError("R354 candidate strategy did not load with the frozen scoring")
    if control is None:
        raise RuntimeError("R354 control strategy did not load")
    expected_neutralize = None
    if strategy.meta.get("neutralize") is not None:
        raise RuntimeError("R354 agreement-gate strategy must not add a neutralization layer")

    plan = StrategyDependencyResolver().resolve(
        strategy,
        params={},
        basic_filter=strategy.basic_filter,
        entry_signals=strategy.entry_signals,
        exit_signals=strategy.exit_signals,
        asset_type="stock",
    )
    panel = BacktestEngine(KlineRepository(DataStore(DATA))).load_panel_for_backtest(
        None,
        PANEL_START,
        END,
        plan,
        asset_type="stock",
    )
    if panel.is_empty():
        raise RuntimeError("R354 preflight panel is empty")

    work = materialize_scoring_columns(
        panel,
        [*strategy.meta["scoring"].keys(), "uf_factor_agreement", "uf_solvency_buffer", "uf_merton_dd"],
    )
    work = attach_industry_l1(work, DATA)
    work = work.with_columns(pl.col("uf_factor_agreement").alias("uf_factor_agreement_raw"))

    filter_expr = strategy.filter_fn(panel, {}) if strategy.filter_fn else pl.lit(True)
    basic_expr = StrategyEngine._basic_filter_expr(panel, strategy.basic_filter) if strategy.basic_filter else pl.lit(True)
    members = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        )
        .unique()
        .sort(["effective_date", "symbol"])
    )
    candidate_expr = filter_expr & basic_expr & _hs300_panel_row_mask(panel, members)
    work = work.with_columns(candidate_expr.fill_null(False).alias("_candidate"))
    work = work.filter(pl.col("date") >= START)

    candidates = work.filter(pl.col("_candidate"))
    if candidates.is_empty():
        raise RuntimeError("R354 preflight found no eligible candidates")

    candidate_counts = candidates.group_by("date").agg(pl.len().alias("candidates")).sort("date")
    coverage = candidates.group_by("date").agg(
        pl.len().alias("candidates"),
        (pl.col("uf_factor_agreement").is_not_null().cast(pl.Float64)).mean().alias("agreement_cov"),
        (pl.col("momentum_60d").is_not_null().cast(pl.Float64)).mean().alias("momentum_cov"),
        (pl.col("uf_quality_value_defensive").is_not_null().cast(pl.Float64)).mean().alias("quality_cov"),
        (pl.col("uf_cash_quality").is_not_null().cast(pl.Float64)).mean().alias("cash_cov"),
        pl.col("uf_factor_agreement").is_finite().cast(pl.Float64).mean().alias("agreement_finite"),
    ).sort("date")
    by_year = (
        candidates.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("candidate_rows"),
            pl.col("date").n_unique().alias("candidate_days"),
            (pl.col("uf_factor_agreement").is_not_null().cast(pl.Float64)).mean().alias("coverage"),
            pl.col("uf_factor_agreement").is_finite().cast(pl.Float64).mean().alias("finite_rate"),
        )
        .sort("year")
    )
    correlations = candidates.group_by("date").agg([
        pl.corr("uf_factor_agreement", "momentum_60d", method="spearman").alias("momentum"),
        pl.corr("uf_factor_agreement", "uf_quality_value_defensive", method="spearman").alias("quality"),
        pl.corr("uf_factor_agreement", "uf_cash_quality", method="spearman").alias("cash"),
        pl.corr("uf_factor_agreement", "uf_low_vol", method="spearman").alias("low_vol"),
        pl.corr("uf_factor_agreement", "uf_solvency_buffer", method="spearman").alias("solvency_buffer"),
        pl.corr("uf_factor_agreement", "uf_merton_dd", method="spearman").alias("merton_dd"),
        pl.corr("uf_factor_agreement_raw", "uf_factor_agreement", method="spearman").alias("raw_vs_neutral"),
    ]).with_columns([
        pl.when(pl.col(name).is_finite()).then(pl.col(name)).otherwise(None).alias(name)
        for name in ("momentum", "quality", "cash", "low_vol", "solvency_buffer", "merton_dd", "raw_vs_neutral")
    ]).drop_nulls()
    corr_names = ["momentum", "quality", "cash", "low_vol", "solvency_buffer", "merton_dd", "raw_vs_neutral"]
    corr_mean = correlations.select([pl.col(name).mean() for name in corr_names]).row(0, named=True)
    corr_median = correlations.select([pl.col(name).median() for name in corr_names]).row(0, named=True)
    corr_abs_p90 = correlations.select([pl.col(name).abs().quantile(0.9) for name in corr_names]).row(0, named=True)

    first_date = candidates.filter(pl.col("uf_factor_agreement").is_not_null())["date"].min()
    max_component_corr = max(abs(float(corr_mean[name])) for name in ("momentum", "quality", "cash", "low_vol", "solvency_buffer", "merton_dd"))
    checks = {
        "D1_formula_compiles": compiled.ok,
        "D2_dependencies_are_backward_pit_inputs": compiled.dependencies == expected_dependencies,
        "D3_candidate_strategy_frozen": strategy.meta.get("scoring") == expected_scoring,
        "D4_no_new_neutralization_layer": strategy.meta.get("neutralize") is expected_neutralize,
        "D5_panel_loaded_with_warmup": panel.height > 0 and panel["date"].min() <= PANEL_START,
        "D6_first_factor_date_at_window_start": first_date is not None and first_date <= START,
        "D7_mean_candidate_coverage": float(coverage["agreement_cov"].mean()) >= 0.99,
        "D8_min_candidate_coverage": float(coverage["agreement_cov"].min()) >= 0.99,
        "D9_every_year_coverage": float(by_year["coverage"].min()) >= 0.99,
        "D10_all_values_finite": float(coverage["agreement_finite"].min()) == 1.0,
        "D11_low_component_correlation": max_component_corr < 0.70,
        "D12_candidate_days": candidates["date"].n_unique() >= 1000,
    }
    after = input_hashes()
    checks["D13_inputs_byte_stable"] = before == after

    payload = {
        "round": "R354",
        "status": "coverage_only_preflight_no_returns",
        "factor": {
            "id": factor["id"],
            "file": str(FACTOR_FILE.relative_to(ROOT)),
            "formula": factor["formula"],
            "direction": factor["direction"],
            "dependencies": sorted(compiled.dependencies),
            "sha256": sha256(FACTOR_FILE),
            "interpretation": "agreement gate: candidate must be above the 60th percentile in at least two of four same-date rankings (momentum, quality-value, cash-quality, low-vol); original C score is unchanged and no neutralization layer is added",
        },
        "candidate_strategy": {
            "id": CANDIDATE_STRATEGY,
            "file": str(strategy.file_path.relative_to(ROOT)),
            "sha256": sha256(Path(strategy.file_path)),
            "scoring": strategy.meta["scoring"],
            "neutralize": strategy.meta.get("neutralize"),
            "max_hold_days": strategy.max_hold_days,
            "stop_loss": strategy.stop_loss,
        },
        "control_strategy": {
            "id": CONTROL_STRATEGY,
            "file": str(control.file_path.relative_to(ROOT)),
            "sha256": sha256(Path(control.file_path)),
            "scoring": control.meta["scoring"],
        },
        "window": {"start": str(START), "end": str(END), "panel_warmup_start": str(PANEL_START)},
        "coverage": {
            "first_factor_date": str(first_date),
            "candidate_rows": candidates.height,
            "candidate_days": candidates["date"].n_unique(),
            "agreement_coverage_mean": float(coverage["agreement_cov"].mean()),
            "agreement_coverage_min": float(coverage["agreement_cov"].min()),
            "component_coverage_mean": coverage.select([
                pl.col(name).mean() for name in ("agreement_cov", "momentum_cov", "quality_cov", "cash_cov")
            ]).row(0, named=True),
            "by_year": by_year.to_dicts(),
            "candidate_count_mean": float(candidate_counts["candidates"].mean()),
            "candidate_count_min": int(candidate_counts["candidates"].min()),
            "candidate_count_max": int(candidate_counts["candidates"].max()),
            "candidate_count_days": candidate_counts.height,
        },
        "factor_structure": {
            "daily_spearman_mean": corr_mean,
            "daily_spearman_median": corr_median,
            "daily_spearman_abs_p90": corr_abs_p90,
            "max_abs_mean_component_correlation": max_component_corr,
            "correlation_days": correlations.height,
            "unique_neutral_values": candidates["uf_factor_agreement"].n_unique(),
        },
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": {"before": before, "after": after, "stable": before == after},
        "no_return_or_backtest_evaluated": True,
        "next": "If all checks pass, freeze R354 portfolio gates before running any return or drawdown backtest.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "status": payload["status"],
        "first_factor_date": payload["coverage"]["first_factor_date"],
        "candidate_rows": payload["coverage"]["candidate_rows"],
        "coverage_min": payload["coverage"]["agreement_coverage_min"],
        "max_component_corr": max_component_corr,
        "checks": checks,
        "all_checks": payload["all_checks"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
