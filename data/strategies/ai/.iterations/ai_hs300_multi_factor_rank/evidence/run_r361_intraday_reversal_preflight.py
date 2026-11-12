from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
OUTPUT = EVIDENCE / "r361_intraday_reversal_preflight.json"
PRE_REG = EVIDENCE / "r361_intraday_reversal_preregistration.json"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY_MEMBERSHIP = DATA / "research/r157_membership/monthly_snapshots.parquet"
FACTOR_ID = "uf_intraday_reversal"
REL_STRENGTH = "ext_alt_hs300_stock_rel_industry_20d"
CANDIDATE = "ai_hs300_industry_relative_strength_intraday_reversal_weak_h20_sl20"
CONTROL = "ai_hs300_industry_relative_strength_quality_weak_h20_sl20"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
PANEL_START = date(2020, 6, 1)
REGIME_FILTER = {"states": ["weak"]}
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]

sys.path.insert(0, str(ROOT / "backend"))


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path, *, include_symlinks: bool = False) -> dict:
    paths = sorted(root.rglob("*"))
    paths = [path for path in paths if path.is_file() and (include_symlinks or not path.is_symlink())]
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def input_fingerprints() -> dict:
    baseline_path = EVIDENCE / "r359_v2_cohort_ensemble_backtest.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_prereg_path = EVIDENCE / "r359_v2_preregistration.json"
    baseline_prereg = json.loads(baseline_prereg_path.read_text(encoding="utf-8"))
    r344_manifest = baseline_prereg["frozen_inputs"]["r344_cache_sha256"]
    r344_valid = {
        relative: sha(ROOT / relative) == expected
        for relative, expected in r344_manifest.items()
    }
    r359_cache_valid = {
        name: (DATA / "research/r359_v2_cache" / name).is_file()
        and (DATA / "research/r359_v2_cache" / name).stat().st_size == int(info["bytes"])
        and sha(DATA / "research/r359_v2_cache" / name) == info["sha256"]
        for name, info in baseline["cohort_cache_manifest"].items()
    }
    return {
        "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
        "production_enriched": tree_hash(DATA / "kline_daily_enriched", include_symlinks=True),
        "raw_daily": tree_hash(DATA / "kline_daily"),
        "alternative_factors": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "alternative_config": sha(DATA / "ext_data/alt_hs300/config.json"),
        "industry_snapshot": sha(DATA / "ext_data/ext_hy_ths/part.parquet"),
        "regime_history": sha(DATA / "regime_history/part.parquet"),
        "membership_daily": sha(MEMBERSHIP),
        "membership_monthly": sha(MONTHLY_MEMBERSHIP),
        "financials": {
            path.relative_to(DATA).as_posix(): sha(path)
            for path in sorted((DATA / "financials").glob("*/part.parquet"))
        },
        "instruments": sha(DATA / "instruments/instruments.parquet"),
        "custom_factors": tree_hash(DATA / "user_data/custom_factors"),
        "defensive_assets": {
            path.name: sha(path)
            for path in sorted((DATA / "research/r331_defensive_assets").glob("*.parquet"))
        },
        "baseline_r359_result": sha(baseline_path),
        "baseline_r359_preregistration": sha(baseline_prereg_path),
        "r344_cache_valid": all(r344_valid.values()),
        "r344_cache_invalid": [name for name, valid in r344_valid.items() if not valid],
        "r359_cohort_cache_valid": all(r359_cache_valid.values()),
        "r359_cohort_cache_invalid": [name for name, valid in r359_cache_valid.items() if not valid],
        "r359_cohort_cache_manifest_count": len(r359_cache_valid),
    }


def weak_regime_entry_mask(labels: list[str], regime: pl.DataFrame, *, start: date, end: date) -> list[bool]:
    from app.backtest.regime_alignment import build_regime_filter_mask

    by_date = {
        str(row["date"])[:10]: {"state": row["state"], "score": row["score"]}
        for row in regime.iter_rows(named=True)
    }
    mask = build_regime_filter_mask(labels, REGIME_FILTER, by_date, required_start=start, required_end=end)
    if mask is None:
        raise RuntimeError("R361 weak-regime filter was not constructed")
    return [bool(value) for value in mask]


def _membership_frame() -> pl.DataFrame:
    monthly = pl.read_parquet(MONTHLY_MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    daily = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    return pl.concat([monthly, daily], how="vertical_relaxed").unique().sort(["effective_date", "symbol"])


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R361 preflight exists; refusing overwrite")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    if sha(PRE_REG) != PRE_REG.with_name(PRE_REG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R361 preregistration checksum mismatch")
    before = input_fingerprints()
    if before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R361 research input fingerprints differ from preregistration")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R361 frozen code changed: {relative}")
    for relative, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R361 frozen artifact changed: {relative}")

    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
    from app.config import settings
    from app.factors.ext_factors import invalidate_ext_caches
    from app.factors.registry import get_factor
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services import regime_builder
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.strategy.scoring import materialize_scoring_columns
    from app.tickflow.repository import DataStore, KlineRepository

    isolation_helper = module(EVIDENCE / "r361_isolation.py", "r361_preflight_isolation")
    previous_data_dir = Path(settings.data_dir)
    previous_regime_cache = (regime_builder._REGIME_HISTORY_CACHE, regime_builder._REGIME_HISTORY_SIG)
    try:
        with tempfile.TemporaryDirectory(prefix="r361_preflight_") as temporary:
            engine_root = Path(temporary) / "engine_data"
            shutil.copytree(R40, engine_root, symlinks=True)
            isolation = isolation_helper.prepare_isolated_data_root(engine_root, DATA)
            if isolation["remaining_top_level_symlinks"] or isolation["alternative_timeseries_is_symlink"]:
                raise RuntimeError("R361 preflight inputs are not isolated")
            settings.data_dir = engine_root
            invalidate_ext_caches(engine_root, keep_strategy_cache=True)
            regime_builder._REGIME_HISTORY_CACHE = None
            regime_builder._REGIME_HISTORY_SIG = None
            load_into_registry(engine_root)
            pipeline.invalidate_custom_signals()
            if get_factor(FACTOR_ID) is None:
                raise RuntimeError("R361 intraday factor failed to register")

            strategy_engine = StrategyEngine(
                strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r361_strategies"],
                override_loader=lambda _sid: {},
                industry_loader=industry_loader_for(engine_root),
            )
            candidate = strategy_engine.get(CANDIDATE)
            control = strategy_engine.get(CONTROL)
            expected_control = {
                "ext_alt_hs300_stock_rel_industry_20d": 0.35,
                "ext_alt_hs300_industry_mom_20d": 0.15,
                "uf_quality_value_defensive": 0.30,
                "uf_cash_quality": 0.20,
            }
            expected_candidate = {
                "ext_alt_hs300_stock_rel_industry_20d": 0.35,
                "ext_alt_hs300_industry_mom_20d": 0.15,
                FACTOR_ID: 0.20,
                "uf_quality_value_defensive": 0.10,
                "uf_cash_quality": 0.20,
            }
            if candidate.meta["scoring"] != expected_candidate or control.meta["scoring"] != expected_control:
                raise RuntimeError("R361 candidate/control scoring contract changed")
            if candidate.basic_filter != control.basic_filter:
                raise RuntimeError("R361 candidate/control filters differ")
            if (candidate.max_hold_days, candidate.stop_loss) != (20, -0.20) or (control.max_hold_days, control.stop_loss) != (20, -0.20):
                raise RuntimeError("R361 candidate/control execution settings changed")

            resolver = StrategyDependencyResolver()
            plans = {
                label: resolver.resolve(
                    strategy,
                    params={},
                    basic_filter=strategy.basic_filter,
                    entry_signals=strategy.entry_signals,
                    exit_signals=strategy.exit_signals,
                    asset_type="stock",
                )
                for label, strategy in (("candidate", candidate), ("control", control))
            }
            if REL_STRENGTH not in plans["candidate"].ext_columns or REL_STRENGTH not in plans["control"].ext_columns:
                raise RuntimeError("R361 industry-relative-strength dependency is missing")
            if FACTOR_ID not in candidate.required_features:
                raise RuntimeError("R361 candidate omitted the new factor dependency")

            panel = BacktestEngine(KlineRepository(DataStore(engine_root))).load_panel_for_backtest(
                None, PANEL_START, END, plans["candidate"], asset_type="stock"
            )
            if panel.is_empty():
                raise RuntimeError("R361 preflight panel is empty")
            panel = panel.sort(["symbol", "date"])
            panel = materialize_scoring_columns(panel, sorted(expected_candidate))
            required_columns = {FACTOR_ID, REL_STRENGTH, "ext_alt_hs300_industry_mom_20d"}
            if not required_columns.issubset(panel.columns):
                missing = sorted(required_columns - set(panel.columns))
                raise RuntimeError(f"R361 panel omitted expected columns: {missing}")

            members = _membership_frame()
            basic = StrategyEngine._basic_filter_expr(panel, candidate.basic_filter)
            candidate_filter = candidate.filter_fn(panel, {}) if candidate.filter_fn else pl.lit(True)
            universe = (_hs300_panel_row_mask(panel, members) & basic & candidate_filter).fill_null(False)
            regime = regime_builder.load_regime_history(engine_root)
            if regime.is_empty() or regime["date"].max() < END:
                raise RuntimeError("R361 regime history does not cover the frozen end date")
            dates = [str(value)[:10] for value in panel["date"].unique().sort().to_list()]
            weak_mask = weak_regime_entry_mask(dates, regime, start=START, end=END)
            weak_by_day = {
                date.fromisoformat(day): active
                for day, active in zip(dates, weak_mask, strict=True)
            }
            daily = (
                panel.with_columns([
                    universe.alias("_universe"),
                    pl.col("date").cast(pl.Date).replace_strict(weak_by_day, default=False).alias("_weak"),
                    pl.col(FACTOR_ID).is_not_null().alias("_factor_present"),
                ])
                .filter(pl.col("date").is_between(START, END) & pl.col("_weak") & pl.col("_universe"))
                .group_by("date")
                .agg([
                    pl.len().alias("candidate_count"),
                    pl.col("_factor_present").mean().alias("factor_coverage"),
                    pl.col(FACTOR_ID).is_finite().mean().alias("factor_finite_rate"),
                ])
                .sort("date")
            )
            if daily.is_empty():
                raise RuntimeError("R361 has no eligible weak-regime B candidates")
            weak_days = [day for day, active in weak_by_day.items() if START <= day <= END and active]
            weak_days_by_phase = {
                phase: sum(day >= date.fromisoformat(phase) for day in weak_days)
                for phase in prereg["primary_window"]["phase_starts"]
            }
            mean_coverage = float(daily["factor_coverage"].mean())
            min_coverage = float(daily["factor_coverage"].min())
            min_candidates = int(daily["candidate_count"].min())
            checks = {
                "D1_candidate_control_scores_and_exit_rules_frozen": candidate.meta["scoring"] == expected_candidate and control.meta["scoring"] == expected_control and candidate.stop_loss == control.stop_loss == -0.20 and candidate.max_hold_days == control.max_hold_days == 20,
                "D2_factor_and_ext_columns_materialized": FACTOR_ID in panel.columns and REL_STRENGTH in panel.columns and panel[FACTOR_ID].is_not_null().sum() > 0,
                "D3_weak_regime_t1_calendar_coverage": bool(weak_days) and len(weak_days) >= 100,
                "D4_daily_candidate_depth": min_candidates >= 10,
                "D5_mean_candidate_factor_coverage_at_least_95pct": mean_coverage >= 0.95,
                "D6_min_candidate_factor_coverage_at_least_90pct": min_coverage >= 0.90,
                "D7_all_phase_starts_have_at_least_5_weak_days": all(count >= 5 for count in weak_days_by_phase.values()),
                "D8_regime_and_membership_cover_frozen_window": regime["date"].max() >= END and members["effective_date"].max() >= END,
            }
            after = input_fingerprints()
            checks["D9_source_inputs_byte_stable"] = before == after
            payload = {
                "round": "R361",
                "status": "coverage_only_preflight_no_returns",
                "preregistration_sha256": sha(PRE_REG),
                "preflight_runner_sha256": sha(Path(__file__).resolve()),
                "backtest_runner_sha256": sha(EVIDENCE / "run_r361_intraday_reversal.py"),
                "test_sha256": sha(ROOT / "backend/tests/test_r361_intraday_reversal.py"),
                "isolation_helper_sha256": sha(EVIDENCE / "r361_isolation.py"),
                "input_fingerprints_before": before,
                "input_fingerprints_after": after,
                "panel_rows": panel.height,
                "panel_start": str(panel["date"].min()),
                "panel_end": str(panel["date"].max()),
                "weak_days": len(weak_days),
                "weak_days_by_phase": weak_days_by_phase,
                "min_weak_candidate_count": min_candidates,
                "mean_weak_candidate_count": float(daily["candidate_count"].mean()),
                "mean_factor_coverage": mean_coverage,
                "min_factor_coverage": min_coverage,
                "min_factor_finite_rate": float(daily["factor_finite_rate"].min()),
                "candidate_ext_columns": sorted(plans["candidate"].ext_columns),
                "control_ext_columns": sorted(plans["control"].ext_columns),
                "candidate_fundamental_columns": sorted(plans["candidate"].fundamental_columns),
                "candidate_warmup_bars": plans["candidate"].warmup_bars,
                "isolated_boundary": isolation,
                "checks": checks,
                "all_checks": all(checks.values()),
                "no_return_or_backtest_evaluated": True,
            }
            OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
            print(json.dumps({"all_checks": payload["all_checks"], "checks": checks, "coverage": {key: payload[key] for key in ("weak_days", "weak_days_by_phase", "min_weak_candidate_count", "mean_weak_candidate_count", "mean_factor_coverage", "min_factor_coverage")}, "no_return_or_backtest_evaluated": True}, ensure_ascii=False, indent=2), flush=True)
            return 0 if payload["all_checks"] else 1
    finally:
        settings.data_dir = previous_data_dir
        regime_builder._REGIME_HISTORY_CACHE, regime_builder._REGIME_HISTORY_SIG = previous_regime_cache
        pipeline.invalidate_custom_signals()
        invalidate_ext_caches(previous_data_dir, keep_strategy_cache=True)
        load_into_registry(previous_data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
