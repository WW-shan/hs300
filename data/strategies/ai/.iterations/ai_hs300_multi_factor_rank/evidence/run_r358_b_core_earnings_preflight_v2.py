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
OUTPUT = EVIDENCE / "r358_b_core_earnings_preflight_v2.json"
PREREG = EVIDENCE / "r358_b_core_earnings_preregistration.json"
FACTOR_FILE = DATA / "user_data/custom_factors/uf_core_earnings_quality.json"
CANDIDATE_STRATEGY = "ai_hs300_industry_relative_strength_quality_core_earnings_gate"
CONTROL_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
PANEL_START = date(2020, 6, 1)
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"

sys.path.insert(0, str(ROOT / "backend"))

from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
from app.factors.dsl import compile_formula
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
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
        "fundamentals_engine": sha256(ROOT / "backend/app/backtest/fundamentals.py"),
        "factor_registry": sha256(ROOT / "backend/app/factors/registry.py"),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R358 preflight exists; refusing overwrite")
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R358 preregistration checksum mismatch")

    before = input_hashes()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg["artifact_sha256"] != {key: value for key, value in before.items() if key in prereg["artifact_sha256"]}:
        raise RuntimeError("R358 preregistered artifact hashes do not match inputs")

    factor = json.loads(FACTOR_FILE.read_text(encoding="utf-8"))
    compiled = compile_formula(factor["formula"])
    if not compiled.ok or compiled.dependencies != {"core_earnings_ratio"}:
        raise RuntimeError(f"R358 factor formula invalid: {compiled.errors}")

    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    strategy = engine.get(CANDIDATE_STRATEGY)
    control = engine.get(CONTROL_STRATEGY)
    expected_scoring = {
        "ext_alt_hs300_stock_rel_industry_20d": 0.35,
        "ext_alt_hs300_industry_mom_20d": 0.15,
        "uf_quality_value_defensive": 0.30,
        "uf_cash_quality": 0.20,
    }
    if strategy is None or strategy.meta.get("scoring") != expected_scoring:
        raise RuntimeError("R358 candidate score changed")
    if control is None or control.meta.get("scoring") != expected_scoring:
        raise RuntimeError("R358 control score changed")
    if strategy.max_hold_days != control.max_hold_days or strategy.max_hold_days != 20:
        raise RuntimeError("R358 hold period changed")
    if strategy.stop_loss != -0.20 or control.stop_loss != -0.20:
        raise RuntimeError("R358 stop-loss contract changed")

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
    if panel.is_empty() or "core_earnings_ratio" not in panel.columns:
        raise RuntimeError("R358 dependency panel is empty or missing core earnings")
    panel = materialize_scoring_columns(panel, list(strategy.meta["scoring"].keys()))

    members = (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        )
        .unique()
        .sort(["effective_date", "symbol"])
    )
    basic_expr = StrategyEngine._basic_filter_expr(panel, strategy.basic_filter)
    membership_expr = _hs300_panel_row_mask(panel, members)
    control_expr = control.filter_fn(panel, {}) if control.filter_fn else pl.lit(True)
    candidate_expr = strategy.filter_fn(panel, {}) if strategy.filter_fn else pl.lit(True)
    work = (
        panel.with_columns(
            (control_expr.fill_null(False) & basic_expr.fill_null(False) & membership_expr).alias("_control_candidate"),
            (candidate_expr.fill_null(False) & basic_expr.fill_null(False) & membership_expr).alias("_candidate"),
        )
        .filter(pl.col("date").is_between(START, END))
    )
    control_candidates = work.filter(pl.col("_control_candidate"))
    candidates = work.filter(pl.col("_candidate"))
    if control_candidates.is_empty() or candidates.is_empty():
        raise RuntimeError("R358 preflight found an empty control or candidate pool")

    coverage_daily = (
        control_candidates.group_by("date")
        .agg(
            pl.len().alias("control_candidates"),
            pl.col("core_earnings_ratio").is_not_null().cast(pl.Float64).mean().alias("core_coverage"),
            pl.col("core_earnings_ratio").is_finite().cast(pl.Float64).mean().alias("core_finite"),
        )
        .sort("date")
    )
    candidate_daily = candidates.group_by("date").agg(pl.len().alias("candidate_candidates")).sort("date")
    retention = (
        coverage_daily.join(candidate_daily, on="date", how="left")
        .with_columns(pl.col("candidate_candidates").fill_null(0))
        .with_columns((pl.col("candidate_candidates") / pl.col("control_candidates")).alias("candidate_retention"))
    )
    by_year = (
        control_candidates.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("control_rows"),
            pl.col("date").n_unique().alias("control_days"),
            pl.col("core_earnings_ratio").is_not_null().cast(pl.Float64).mean().alias("coverage"),
            pl.col("core_earnings_ratio").is_finite().cast(pl.Float64).mean().alias("finite_rate"),
        )
        .sort("year")
    )
    correlations = (
        control_candidates.filter(pl.col("core_earnings_ratio").is_not_null())
        .group_by("date")
        .agg([
            pl.corr("core_earnings_ratio", "ext_alt_hs300_stock_rel_industry_20d", method="spearman").alias("stock_rel_industry"),
            pl.corr("core_earnings_ratio", "ext_alt_hs300_industry_mom_20d", method="spearman").alias("industry_mom"),
            pl.corr("core_earnings_ratio", "uf_quality_value_defensive", method="spearman").alias("quality"),
            pl.corr("core_earnings_ratio", "uf_cash_quality", method="spearman").alias("cash"),
        ])
        .drop_nulls()
    )
    corr_names = ["stock_rel_industry", "industry_mom", "quality", "cash"]
    corr_mean = correlations.select([pl.col(name).mean() for name in corr_names]).row(0, named=True)
    corr_median = correlations.select([pl.col(name).median() for name in corr_names]).row(0, named=True)
    corr_abs_p90 = correlations.select([pl.col(name).abs().quantile(0.9) for name in corr_names]).row(0, named=True)
    max_component_corr = max(abs(float(corr_mean[name])) for name in corr_names)
    first_date = control_candidates.filter(pl.col("core_earnings_ratio").is_not_null())["date"].min()

    checks = {
        "D1_factor_formula_compiles": compiled.ok,
        "D2_dependencies_are_existing_pit_inputs": compiled.dependencies == {"core_earnings_ratio"},
        "D3_candidate_preserves_B_score_and_execution": (
            strategy.meta["scoring"] == control.meta["scoring"]
            and strategy.meta.get("params") == control.meta.get("params")
            and strategy.basic_filter == control.basic_filter
        ),
        "D4_first_core_value_on_or_before_start": first_date is not None and first_date <= START,
        "D5_mean_core_coverage_among_B_candidates_at_least_0.90": float(coverage_daily["core_coverage"].mean()) >= 0.90,
        "D6_min_core_coverage_among_B_candidates_at_least_0.90": float(coverage_daily["core_coverage"].min()) >= 0.90,
        "D7_every_year_coverage_at_least_0.90": float(by_year["coverage"].min()) >= 0.90,
        "D8_all_nonnull_core_values_finite": float(coverage_daily["core_finite"].min()) == 1.0,
        "D9_max_abs_mean_rank_correlation_with_B_legs_below_0.70": max_component_corr < 0.70,
        "D10_mean_candidate_retention_at_least_0.80": float(retention["candidate_retention"].mean()) >= 0.80,
        "D11_candidate_days_equal_control_candidate_days_and_at_least_1000": candidates["date"].n_unique() == control_candidates["date"].n_unique() and candidates["date"].n_unique() >= 1000,
    }
    after = input_hashes()
    checks["D12_inputs_byte_stable"] = before == after

    payload = {
        "round": "R358",
        "status": "coverage_only_preflight_no_returns",
        "preregistration_sha256": sha256(PREREG),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "factor": {
            "id": factor["id"],
            "file": str(FACTOR_FILE.relative_to(ROOT)),
            "formula": factor["formula"],
            "dependencies": sorted(compiled.dependencies),
            "sha256": sha256(FACTOR_FILE),
            "threshold": 0.5,
        },
        "candidate_strategy": {
            "id": CANDIDATE_STRATEGY,
            "file": str(Path(strategy.file_path).relative_to(ROOT)),
            "sha256": sha256(Path(strategy.file_path)),
            "scoring": strategy.meta["scoring"],
            "max_hold_days": strategy.max_hold_days,
            "stop_loss": strategy.stop_loss,
        },
        "control_strategy": {
            "id": CONTROL_STRATEGY,
            "file": str(Path(control.file_path).relative_to(ROOT)),
            "sha256": sha256(Path(control.file_path)),
            "scoring": control.meta["scoring"],
            "max_hold_days": control.max_hold_days,
            "stop_loss": control.stop_loss,
        },
        "window": {"start": str(START), "end": str(END), "panel_warmup_start": str(PANEL_START)},
        "coverage": {
            "first_core_date": str(first_date),
            "control_candidate_rows": control_candidates.height,
            "control_candidate_days": control_candidates["date"].n_unique(),
            "candidate_rows": candidates.height,
            "candidate_days": candidates["date"].n_unique(),
            "mean_core_coverage": float(coverage_daily["core_coverage"].mean()),
            "min_core_coverage": float(coverage_daily["core_coverage"].min()),
            "mean_candidate_retention": float(retention["candidate_retention"].mean()),
            "min_candidate_retention": float(retention["candidate_retention"].min()),
            "median_candidates_per_day": float(candidate_daily["candidate_candidates"].median()),
            "min_candidates_per_day": int(candidate_daily["candidate_candidates"].min()),
            "by_year": by_year.to_dicts(),
        },
        "factor_structure": {
            "daily_spearman_mean": corr_mean,
            "daily_spearman_median": corr_median,
            "daily_spearman_abs_p90": corr_abs_p90,
            "max_abs_mean_component_correlation": max_component_corr,
            "correlation_days": correlations.height,
        },
        "checks": checks,
        "all_checks": all(checks.values()),
        "input_isolation": {"before": before, "after": after, "stable": before == after},
        "no_return_or_backtest_evaluated": True,
        "next": "Only if all preflight checks pass may the frozen R358 performance gates be evaluated.",
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({
        "all_checks": payload["all_checks"],
        "coverage": payload["coverage"],
        "factor_structure": payload["factor_structure"],
        "checks": checks,
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["all_checks"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
