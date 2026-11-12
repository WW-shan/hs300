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
PRE_REG = EVIDENCE / "r360_v2_preregistration.json"
OUTPUT = EVIDENCE / "r360_v2_preflight.json"
STRATEGY_PATH = EVIDENCE / "r360_strategies/ai_hs300_industry_fade_quality_h20_sl20.py"
FACTOR_PATH = DATA / "user_data/custom_factors/uf_industry_relative_strength_change_5d.json"
SIGNAL_PATH = DATA / "user_data/custom_signals/r360_rel_strength_fade.json"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
R40 = DATA / "research/r40_engine_data"
PRODUCTION = DATA / "kline_daily_enriched"
R344_CACHE = DATA / "research/r344_cache"
PHASES = [date(2022, 5, 5), date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 11, 5), date(2026, 1, 5)]
START = PHASES[0]
END = date(2026, 11, 5)
CONTROL_STRATEGY_ID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
FACTOR_ID = "uf_industry_relative_strength_change_5d"
REL_FACTOR = "ext_alt_hs300_stock_rel_industry_20d"
SIGNAL_COLUMN = "csg_r360_rel_strength_fade"

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
    total_bytes = 0
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        total_bytes += path.stat().st_size
    return {"files": len(paths), "bytes": total_bytes, "sha256": digest.hexdigest()}


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
    r344_hashes = baseline_prereg["frozen_inputs"]["r344_cache_sha256"]
    r344_cache_valid = {
        relative: sha(ROOT / relative) == expected
        for relative, expected in r344_hashes.items()
    }
    cohort_cache_valid = {}
    for name, info in baseline["cohort_cache_manifest"].items():
        path = DATA / "research/r359_v2_cache" / name
        cohort_cache_valid[name] = (
            path.is_file()
            and path.stat().st_size == int(info["bytes"])
            and sha(path) == info["sha256"]
        )
    return {
        "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
        "raw_daily": tree_hash(DATA / "kline_daily"),
        "production_enriched": tree_hash(PRODUCTION, include_symlinks=True),
        "alternative_factors": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "alternative_config": sha(DATA / "ext_data/alt_hs300/config.json"),
        "industry_snapshot": sha(DATA / "ext_data/ext_hy_ths/part.parquet"),
        "membership": sha(MEMBERSHIP),
        "financials": {
            path.relative_to(DATA).as_posix(): sha(path)
            for path in sorted((DATA / "financials").glob("*/part.parquet"))
        },
        "instruments": sha(DATA / "instruments/instruments.parquet"),
        "custom_factors": tree_hash(DATA / "user_data/custom_factors"),
        "custom_signals": tree_hash(DATA / "user_data/custom_signals"),
        "defensive_assets": {
            path.name: sha(path)
            for path in sorted((DATA / "research/r331_defensive_assets").glob("*.parquet"))
        },
        "r359_result": sha(baseline_path),
        "r359_preregistration": sha(baseline_prereg_path),
        "r344_cache_valid": all(r344_cache_valid.values()),
        "r344_cache_invalid": [name for name, valid in r344_cache_valid.items() if not valid],
        "r359_cohort_cache_valid": all(cohort_cache_valid.values()),
        "r359_cohort_cache_invalid": [name for name, valid in cohort_cache_valid.items() if not valid],
        "r359_cohort_cache_manifest_count": len(cohort_cache_valid),
    }


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R360 v2 preflight exists; refusing overwrite")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    frozen_before = input_fingerprints()
    if frozen_before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R360 research input fingerprints differ from preregistration")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R360 frozen code input changed: {relative}")
    strategy_sha = sha(STRATEGY_PATH)
    factor_sha = sha(FACTOR_PATH)
    signal_sha = sha(SIGNAL_PATH)
    baseline_sha = sha(EVIDENCE / "r359_v2_cohort_ensemble_backtest.json")
    if baseline_sha != prereg["frozen_inputs"]["baseline_result_sha256"]:
        raise RuntimeError("R359 v2 baseline hash mismatch")

    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
    from app.config import settings
    from app.factors.ext_factors import ensure_synced, invalidate_ext_caches
    from app.factors.registry import get_factor, register_factor
    from app.factors.store import load_into_registry, to_spec
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy import config as strategy_config
    from app.strategy import custom_signals
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    isolation = module(EVIDENCE / "r360_isolation.py", "r360_preflight_isolation")
    previous_data_dir = Path(settings.data_dir)
    original_load = custom_signals.load_all
    try:
        with tempfile.TemporaryDirectory(prefix="r360_preflight_snapshot_") as temporary:
            isolated_root = Path(temporary) / "engine_data"
            shutil.copytree(R40, isolated_root, symlinks=True)
            isolation_report = isolation.prepare_isolated_data_root(isolated_root, DATA)
            if not isolation_report["isolated"] or isolation_report["remaining_top_level_symlinks"]:
                raise RuntimeError("R360 preflight snapshot still contains linked source inputs")
            settings.data_dir = isolated_root
            invalidate_ext_caches(isolated_root, keep_strategy_cache=True)
            ensure_synced(isolated_root)
            pipeline.invalidate_custom_signals()
            load_into_registry(isolated_root)

            definition_path = isolated_root / "user_data/custom_factors/uf_industry_relative_strength_change_5d.json"
            signal_path = isolated_root / "user_data/custom_signals/r360_rel_strength_fade.json"
            definition = json.loads(definition_path.read_text(encoding="utf-8"))
            spec = to_spec(definition)
            if get_factor(FACTOR_ID) is None:
                register_factor(spec)
            elif get_factor(FACTOR_ID) != spec:
                raise RuntimeError("R360 factor definition is inconsistent with the registry")
            signal = json.loads(signal_path.read_text(encoding="utf-8"))
            custom_signals.validate(signal)
            expressions = custom_signals.build_expressions([signal])
            if SIGNAL_COLUMN not in expressions:
                raise RuntimeError("R360 exit signal did not compile")

            engine = StrategyEngine(
                strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r360_strategies"],
                override_loader=lambda strategy_id: strategy_config.load_override(isolated_root, strategy_id),
                industry_loader=industry_loader_for(isolated_root),
            )
            strategy = engine.get("ai_hs300_industry_fade_quality_h20_sl20")
            plan = StrategyDependencyResolver().resolve(
                strategy,
                params={},
                basic_filter=strategy.basic_filter,
                entry_signals=strategy.entry_signals,
                exit_signals=strategy.exit_signals,
                asset_type="stock",
            )
            if strategy.max_hold_days != 20 or strategy.stop_loss != -0.20:
                raise RuntimeError("candidate hold/stop mismatch")
            if set(strategy.exit_signals) != {SIGNAL_COLUMN}:
                raise RuntimeError("candidate exit signal mismatch")
            if SIGNAL_COLUMN not in plan.signal_columns or REL_FACTOR not in plan.ext_columns:
                raise RuntimeError("resolved plan is missing signal/industry factor dependencies")

            panel = BacktestEngine(KlineRepository(DataStore(isolated_root))).load_panel_for_backtest(
                None, date(2020, 6, 1), END, plan, asset_type="stock"
            )
            if panel.is_empty():
                raise RuntimeError("R360 isolated preflight panel is empty")
            panel = panel.sort(["symbol", "date"])
            memberships = pl.read_parquet(
                MEMBERSHIP, columns=["query_date", "symbol"]
            ).select(
                pl.col("query_date").cast(pl.Date).alias("effective_date"),
                pl.col("symbol").cast(pl.String),
            ).unique()
            basic = StrategyEngine._basic_filter_expr(panel, strategy.basic_filter)
            candidate = strategy.filter_fn(panel, {})
            panel = panel.with_columns(
                (basic & candidate & _hs300_panel_row_mask(panel, memberships))
                .fill_null(False)
                .alias("_candidate")
            )
            panel = custom_signals.materialize_factor_columns(panel, expressions)
            panel = custom_signals.inject(panel, expressions)
            if FACTOR_ID not in panel.columns or SIGNAL_COLUMN not in panel.columns:
                raise RuntimeError("R360 factor/signal did not materialize in isolated history panel")

            signal_checks = panel.group_by("date").agg([
                pl.col("_candidate").sum().alias("candidate_count"),
                (pl.col("_candidate") & pl.col(SIGNAL_COLUMN).fill_null(False)).sum().alias("signal_count"),
                (
                    pl.col("_candidate")
                    & pl.col(REL_FACTOR).is_not_null()
                    & pl.col(FACTOR_ID).is_not_null()
                ).sum().alias("feature_coverage"),
            ])
            signal_map = {row["date"]: row for row in signal_checks.iter_rows(named=True)}
            sessions = pl.read_parquet(
                R344_CACHE / f"{CONTROL_STRATEGY_ID}_2022-05-05_2x_p10.parquet",
                columns=["date"],
            )["date"].cast(pl.Date).to_list()
            cohort_starts = {}
            for phase in PHASES:
                index = sessions.index(phase)
                cohort_starts[str(phase)] = [str(sessions[index + offset]) for offset in (0, 20, 40)]
            activation_dates = sorted({date.fromisoformat(day) for values in cohort_starts.values() for day in values})
            coverage = {
                str(day): {
                    "candidate_count": int(signal_map.get(day, {}).get("candidate_count", 0)),
                    "signal_count": int(signal_map.get(day, {}).get("signal_count", 0)),
                    "feature_coverage": int(signal_map.get(day, {}).get("feature_coverage", 0)),
                }
                for day in activation_dates
            }
            if sum(values["signal_count"] for values in coverage.values()) <= 0:
                raise RuntimeError("R360 fixed exit signal never triggers on any cohort activation date")
            window = panel.filter(pl.col("date").is_between(START, END))
            candidate_rows = int(window["_candidate"].sum())
            factor_rows = int(window[FACTOR_ID].is_not_null().sum())
            relative_rows = int(window[REL_FACTOR].is_not_null().sum())
            final_inputs = input_fingerprints()
            input_trees = {
                "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
                "production_enriched": tree_hash(PRODUCTION, include_symlinks=True),
            }
            isolation_checks = {
                "no_source_symlinks": not isolation_report["remaining_top_level_symlinks"],
                "factor_and_signal_copied": (
                    sha(definition_path) == factor_sha
                    and sha(signal_path) == signal_sha
                ),
                "source_inputs_unchanged": final_inputs == frozen_before,
                "all_18_activation_dates_have_candidates": len(coverage) == 18 and all(
                    values["candidate_count"] >= 10 for values in coverage.values()
                ),
                "all_candidates_have_factor_coverage": all(
                    values["feature_coverage"] == values["candidate_count"] for values in coverage.values()
                ),
                "r344_cache_manifest_verified": frozen_before["r344_cache_valid"],
                "r359_cache_manifest_verified": frozen_before["r359_cohort_cache_valid"],
            }
            all_pass = (
                candidate_rows > 0 and factor_rows > 0 and relative_rows > 0
                and all(isolation_checks.values())
            )
            payload = {
                "round": "R360-v2",
                "all_pass": all_pass,
                "returns_or_backtest_read": False,
                "preregistration_sha256": sha(PRE_REG),
                "preflight_runner_sha256": sha(Path(__file__).resolve()),
                "backtest_runner_sha256": prereg["frozen_inputs"]["runner_sha256"],
                "isolation_helper_sha256": sha(EVIDENCE / "r360_isolation.py"),
                "test_sha256": sha(ROOT / "backend/tests/test_r360_industry_fade.py"),
                "strategy_sha256": strategy_sha,
                "factor_definition_sha256": factor_sha,
                "signal_definition_sha256": signal_sha,
                "baseline_result_sha256": baseline_sha,
                "panel_rows": panel.height,
                "panel_start": str(panel["date"].min()),
                "panel_end": str(panel["date"].max()),
                "candidate_rows_primary_window": candidate_rows,
                "factor_nonnull_rows_primary_window": factor_rows,
                "relative_strength_nonnull_rows_primary_window": relative_rows,
                "fixed_phase_cohort_starts": cohort_starts,
                "candidate_coverage_at_all_18_activation_dates": coverage,
                "input_fingerprints_before": frozen_before,
                "input_fingerprints_after": final_inputs,
                "input_trees_before": input_trees,
                "input_trees_after": input_trees,
                "isolated_data_boundary_checks": isolation_checks,
                "isolation_report": {
                    key: value for key, value in isolation_report.items() if key != "isolated_data_root"
                },
                "historical_membership_sha256": sha(MEMBERSHIP),
                "alt_hs300_timeseries": tree_hash(isolated_root / "ext_data/alt_hs300/timeseries"),
                "candidate_strategy_id": strategy.meta["id"],
                "resolved_signal_columns": sorted(plan.signal_columns),
                "resolved_ext_columns": sorted(plan.ext_columns),
                "resolved_warmup_bars": plan.warmup_bars,
                "status": "passed_preflight_returns_not_read" if all_pass else "failed_preflight_returns_not_read",
            }
            OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            digest = sha(OUTPUT)
            OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{digest}  {OUTPUT.name}\n", encoding="ascii")
            print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
            return 0 if all_pass else 1
    finally:
        settings.data_dir = previous_data_dir
        invalidate_ext_caches(previous_data_dir, keep_strategy_cache=True)
        custom_signals.load_all = original_load
        pipeline.invalidate_custom_signals()
        ensure_synced(previous_data_dir)
        load_into_registry(previous_data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
