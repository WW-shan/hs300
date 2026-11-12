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
OUTPUT = EVIDENCE / "r368_capacity_preflight.json"
PRE_REG = EVIDENCE / "r368_capacity_preregistration.json"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY_MEMBERSHIP = DATA / "research/r157_membership/monthly_snapshots.parquet"
FACTOR_ID = "cf_weak_reversal_blend"
CANDIDATE = "ai_hs300_weak_reversal_blend_size_h20_sl20_r368"
CONTROL = "ai_hs300_quality_size_h20_sl20_r368"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
PANEL_START = date(2020, 6, 1)
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


def input_fingerprints() -> dict:
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
        "instruments": sha(DATA / "instruments/instruments.parquet"),
        "custom_factors": tree_hash(DATA / "user_data/custom_factors"),
        "custom_signals": tree_hash(DATA / "user_data/custom_signals"),
        "r367_result": sha(EVIDENCE / "r367_size_neutralization_backtest.json"),
        "r367_preregistration": sha(EVIDENCE / "r367_size_neutralization_preregistration.json"),
        "r367_status": sha(EVIDENCE / "r367_final_status.json"),
    }


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def membership_frame() -> pl.DataFrame:
    monthly = pl.read_parquet(MONTHLY_MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    daily = pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"]).select(
        pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String)
    )
    return pl.concat([monthly, daily], how="vertical_relaxed").unique().sort(["effective_date", "symbol"])


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R368 preflight exists; refusing overwrite")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    if sha(PRE_REG) != PRE_REG.with_name(PRE_REG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R368 preregistration checksum mismatch")
    before = input_fingerprints()
    if before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R368 data fingerprints differ from preregistration")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R368 code changed after registration: {relative}")
    for relative, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R368 strategy changed after registration: {relative}")

    from app.backtest.engine import BacktestEngine
    from app.backtest.regime_alignment import build_regime_filter_mask
    from app.backtest.strategy import StrategyDependencyResolver, _hs300_panel_row_mask
    from app.config import settings
    from app.factors.ext_factors import invalidate_ext_caches
    from app.factors.registry import get_factor
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services import regime_builder
    from app.strategy.engine import StrategyEngine
    from app.strategy.scoring import effective_neutralize, materialize_scoring_columns
    from app.tickflow.repository import DataStore, KlineRepository

    previous_data_dir = Path(settings.data_dir)
    previous_regime_cache = (regime_builder._REGIME_HISTORY_CACHE, regime_builder._REGIME_HISTORY_SIG)
    isolation_helper = module(EVIDENCE / "r365_isolation.py", "r368_isolation")
    try:
        with tempfile.TemporaryDirectory(prefix="r368_preflight_") as temporary:
            engine_root = Path(temporary) / "engine_data"
            shutil.copytree(R40, engine_root, symlinks=True)
            isolation = isolation_helper.prepare_isolated_data_root(engine_root, DATA)
            if isolation["remaining_top_level_symlinks"]:
                raise RuntimeError("R368 preflight data not fully isolated")
            settings.data_dir = engine_root
            invalidate_ext_caches(engine_root, keep_strategy_cache=True)
            regime_builder._REGIME_HISTORY_CACHE = None
            regime_builder._REGIME_HISTORY_SIG = None
            load_into_registry(engine_root)
            pipeline.invalidate_custom_signals()
            if get_factor(FACTOR_ID) is None:
                raise RuntimeError("R368 composite factor failed to register")
            strategy_engine = StrategyEngine(strategy_dirs=[EVIDENCE / "r368_strategies"], override_loader=lambda _sid: {})
            candidate, control = strategy_engine.get(CANDIDATE), strategy_engine.get(CONTROL)
            if candidate.meta["scoring"] != control.meta["scoring"] and set(candidate.meta["scoring"]) == set(control.meta["scoring"]):
                raise RuntimeError("R368 paired strategy score feature set changed unexpectedly")
            if candidate.meta["scoring"][FACTOR_ID] != 0.20 or candidate.meta["scoring"]["uf_quality_value_defensive"] != 0.10:
                raise RuntimeError("R368 candidate score contract changed")
            if control.meta["scoring"].get(FACTOR_ID) is not None or control.meta["scoring"]["uf_quality_value_defensive"] != 0.30:
                raise RuntimeError("R368 quality control score contract changed")
            if candidate.basic_filter != control.basic_filter or candidate.max_hold_days != control.max_hold_days or candidate.stop_loss != control.stop_loss:
                raise RuntimeError("R368 capacity pair rule mismatch")
            if candidate.max_hold_days != 20 or candidate.stop_loss != -0.20:
                raise RuntimeError("R368 base capacity pair must use frozen 20-day/-20% rule")
            expected_neutral = {"industry": None, **prereg["rule"]["neutralization"]}
            if effective_neutralize(candidate.meta) != expected_neutral or effective_neutralize(control.meta) != expected_neutral:
                raise RuntimeError("R368 size-neutral score rule changed")
            resolver = StrategyDependencyResolver()
            plans = {label: resolver.resolve(strategy, params={}, basic_filter=strategy.basic_filter,
                                             entry_signals=strategy.entry_signals, exit_signals=strategy.exit_signals,
                                             asset_type="stock")
                     for label, strategy in (("candidate", candidate), ("control", control))}
            panel = BacktestEngine(KlineRepository(DataStore(engine_root))).load_panel_for_backtest(None, PANEL_START, END, plans["candidate"], asset_type="stock")
            if panel.is_empty():
                raise RuntimeError("R368 panel is empty")
            panel = materialize_scoring_columns(panel.sort(["symbol", "date"]), sorted(set(candidate.meta["scoring"]) | {"log_float_mv"}))
            required = {FACTOR_ID, "uf_intraday_reversal", "uf_close_position_reversal", "log_float_mv"}
            if not required.issubset(panel.columns):
                raise RuntimeError(f"R368 required columns missing: {sorted(required - set(panel.columns))}")
            members = membership_frame()
            basic = StrategyEngine._basic_filter_expr(panel, candidate.basic_filter)
            filter_expr = candidate.filter_fn(panel, {}) if candidate.filter_fn else pl.lit(True)
            universe = (_hs300_panel_row_mask(panel, members) & basic & filter_expr).fill_null(False)
            regime = regime_builder.load_regime_history(engine_root)
            if regime.is_empty() or regime["date"].max() < END:
                raise RuntimeError("R368 regime history incomplete")
            labels = [str(value)[:10] for value in panel["date"].unique().sort().to_list()]
            by_date = {str(row["date"])[:10]: {"state": row["state"], "score": row["score"]} for row in regime.iter_rows(named=True)}
            mask = build_regime_filter_mask(labels, {"states": ["weak"]}, by_date, required_start=START, required_end=END)
            if mask is None:
                raise RuntimeError("R368 T-1 weak mask unavailable")
            weak = {date.fromisoformat(day): bool(flag) for day, flag in zip(labels, mask, strict=True)}
            eligible = panel.with_columns([
                universe.alias("_universe"), pl.col("date").cast(pl.Date).replace_strict(weak, default=False).alias("_weak"),
                pl.col(FACTOR_ID).is_not_null().alias("_blend"), pl.col("log_float_mv").is_not_null().alias("_size"),
            ]).filter(pl.col("date").is_between(START, END) & pl.col("_weak") & pl.col("_universe"))
            daily = eligible.group_by("date").agg([pl.len().alias("n"),pl.col("_blend").mean().alias("blend_coverage"),pl.col("_size").mean().alias("size_coverage")]).sort("date")
            weak_days = [day for day, flag in weak.items() if START <= day <= END and flag]
            phase_counts = {phase.isoformat(): sum(day >= phase for day in weak_days) for phase in PHASES}
            after = input_fingerprints()
            checks = {
                "D1_candidate_control_and_capacity_variants_frozen": candidate.basic_filter == control.basic_filter and candidate.stop_loss == control.stop_loss == -0.20 and candidate.max_hold_days == control.max_hold_days == 20,
                "D2_composite_and_size_factor_materialized": required.issubset(panel.columns),
                "D3_weak_days_at_least_100": len(weak_days) >= 100,
                "D4_min_daily_candidate_count_at_least_10": int(daily["n"].min()) >= 10,
                "D5_blend_and_size_coverage_at_least_95pct": float(daily["blend_coverage"].mean()) >= .95 and float(daily["size_coverage"].mean()) >= .95,
                "D6_all_fixed_starts_have_at_least_5_weak_days": all(count >= 5 for count in phase_counts.values()),
                "D7_regime_and_membership_cover_window": regime["date"].max() >= END and members["effective_date"].max() >= END,
                "D8_source_inputs_byte_stable": before == after,
            }
            payload = {"round":"R368","status":"coverage_only_preflight_no_returns","preregistration_sha256":sha(PRE_REG),
                       "preflight_runner_sha256":sha(Path(__file__).resolve()),"backtest_runner_sha256":sha(EVIDENCE/"run_r368_capacity.py"),
                       "test_sha256":sha(ROOT/"backend/tests/test_r368_capacity.py"),"input_fingerprints_before":before,"input_fingerprints_after":after,
                       "panel_rows":panel.height,"panel_start":str(panel["date"].min()),"panel_end":str(panel["date"].max()),
                       "weak_days":len(weak_days),"weak_days_by_phase":phase_counts,"min_daily_candidates":int(daily["n"].min()),
                       "mean_blend_coverage":float(daily["blend_coverage"].mean()),"mean_size_coverage":float(daily["size_coverage"].mean()),
                       "candidate_capacity_sweep":[10,20],"candidate_control_features":{"candidate":sorted(candidate.meta["scoring"]),"control":sorted(control.meta["scoring"])},
                       "isolation":isolation,"checks":checks,"all_checks":all(checks.values()),"no_return_or_backtest_evaluated":True}
            OUTPUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
            OUTPUT.with_name(OUTPUT.name+".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n",encoding="ascii")
            print(json.dumps({"all_checks":payload["all_checks"],"checks":checks,"coverage":{k:payload[k] for k in ("weak_days","min_daily_candidates","mean_blend_coverage","mean_size_coverage")},"no_return_or_backtest_evaluated":True},ensure_ascii=False,indent=2),flush=True)
            return 0 if payload["all_checks"] else 1
    finally:
        settings.data_dir=previous_data_dir
        regime_builder._REGIME_HISTORY_CACHE,regime_builder._REGIME_HISTORY_SIG=previous_regime_cache
        pipeline.invalidate_custom_signals()
        invalidate_ext_caches(previous_data_dir,keep_strategy_cache=True)
        load_into_registry(previous_data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
