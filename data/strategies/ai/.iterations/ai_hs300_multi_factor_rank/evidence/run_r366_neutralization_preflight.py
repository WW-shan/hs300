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
OUTPUT = EVIDENCE / "r366_neutralization_preflight_v2.json"
PRE_REG = EVIDENCE / "r366_neutralization_preregistration_v2.json"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
MONTHLY_MEMBERSHIP = DATA / "research/r157_membership/monthly_snapshots.parquet"
FACTOR_ID = "cf_weak_reversal_blend"
START = date(2022, 5, 5)
END = date(2026, 10, 14)
PANEL_START = date(2020, 6, 1)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), date(2025, 10, 14), date(2026, 1, 5)]
VARIANTS = {
    "industry": {
        "candidate": "ai_hs300_weak_reversal_blend_industry_h20_sl20",
        "control": "ai_hs300_quality_industry_h20_sl20",
        "neutralize": {"industry": "industry_l1", "styles": [], "exclude": [], "shrink": 1.0},
    },
    "industry_size": {
        "candidate": "ai_hs300_weak_reversal_blend_industry_size_h20_sl20",
        "control": "ai_hs300_quality_industry_size_h20_sl20",
        "neutralize": {"industry": "industry_l1", "styles": ["log_float_mv"], "exclude": [], "shrink": 1.0},
    },
}

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
        "financials": {
            path.relative_to(DATA).as_posix(): sha(path)
            for path in sorted((DATA / "financials").glob("*/part.parquet"))
        },
        "instruments": sha(DATA / "instruments/instruments.parquet"),
        "custom_factors": tree_hash(DATA / "user_data/custom_factors"),
        "custom_signals": tree_hash(DATA / "user_data/custom_signals"),
        "r365_result": sha(EVIDENCE / "r365_weak_reversal_20d_backtest.json"),
        "r365_preregistration": sha(EVIDENCE / "r365_weak_reversal_20d_preregistration.json"),
        "r365_gate_audit": sha(EVIDENCE / "r365_preregistered_gate_audit.json"),
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
        raise RuntimeError("R366 preflight exists; refusing overwrite")
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    if sha(PRE_REG) != PRE_REG.with_name(PRE_REG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R366 preregistration checksum mismatch")
    before = input_fingerprints()
    if before != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R366 research input fingerprints differ from preregistration")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R366 frozen implementation changed: {relative}")
    for relative, expected in prereg["frozen_artifacts"].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError(f"R366 frozen strategy changed: {relative}")
    for key, relative in (
        ("preflight_runner_sha256", "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r366_neutralization_preflight.py"),
        ("backtest_runner_sha256", "data/strategies/ai/.iterations/ai_hs300_multi_factor_rank/evidence/run_r366_neutralization.py"),
        ("test_sha256", "backend/tests/test_r366_neutralization.py"),
    ):
        if prereg["frozen_inputs"].get(key) != sha(ROOT / relative):
            raise RuntimeError(f"R366 runner/test hash mismatch: {key}")

    from app.backtest.engine import BacktestEngine
    from app.backtest.regime_alignment import build_regime_filter_mask
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

    prior_dir = Path(settings.data_dir)
    prior_regime_cache = (regime_builder._REGIME_HISTORY_CACHE, regime_builder._REGIME_HISTORY_SIG)
    iso = module(EVIDENCE / "r365_isolation.py", "r366_preflight_isolation")
    try:
        with tempfile.TemporaryDirectory(prefix="r366_preflight_") as temporary:
            engine_root = Path(temporary) / "engine_data"
            shutil.copytree(R40, engine_root, symlinks=True)
            isolation_report = iso.prepare_isolated_data_root(engine_root, DATA)
            if isolation_report["remaining_top_level_symlinks"] or isolation_report["alternative_timeseries_is_symlink"]:
                raise RuntimeError("R366 isolated data root retains source-linked inputs")
            settings.data_dir = engine_root
            invalidate_ext_caches(engine_root, keep_strategy_cache=True)
            regime_builder._REGIME_HISTORY_CACHE = None
            regime_builder._REGIME_HISTORY_SIG = None
            load_into_registry(engine_root)
            pipeline.invalidate_custom_signals()
            if get_factor(FACTOR_ID) is None:
                raise RuntimeError("R366 sentiment composite failed to register")
            strategies = StrategyEngine(
                strategy_dirs=[EVIDENCE / "r366_strategies"],
                override_loader=lambda _sid: {},
                industry_loader=industry_loader_for(engine_root),
            )
            candidate = strategies.get(VARIANTS["industry_size"]["candidate"])
            control = strategies.get(VARIANTS["industry_size"]["control"])
            resolver = StrategyDependencyResolver()
            plans = {
                label: resolver.resolve(
                    strategy, params={}, basic_filter=strategy.basic_filter,
                    entry_signals=strategy.entry_signals, exit_signals=strategy.exit_signals,
                    asset_type="stock",
                )
                for label, strategy in (("candidate", candidate), ("control", control))
            }
            panel = BacktestEngine(KlineRepository(DataStore(engine_root))).load_panel_for_backtest(
                None, PANEL_START, END, plans["candidate"], asset_type="stock"
            )
            if panel.is_empty():
                raise RuntimeError("R366 preflight panel is empty")
            panel = materialize_scoring_columns(
                panel.sort(["symbol", "date"]),
                sorted(CANDIDATE_SCORING.keys() | {"log_float_mv"}),
            )
            from app.services.industry_map import attach_industry_l1
            panel = attach_industry_l1(panel, engine_root)
            members = membership_frame()
            basic = StrategyEngine._basic_filter_expr(panel, candidate.basic_filter)
            filter_expr = candidate.filter_fn(panel, {}) if candidate.filter_fn else pl.lit(True)
            universe = (_hs300_panel_row_mask(panel, members) & basic & filter_expr).fill_null(False)
            regime = regime_builder.load_regime_history(engine_root)
            if regime.is_empty() or regime["date"].max() < END:
                raise RuntimeError("R366 regime history misses frozen end date")
            labels = [str(value)[:10] for value in panel["date"].unique().sort().to_list()]
            regime_by_date = {
                str(row["date"])[:10]: {"state": row["state"], "score": row["score"]}
                for row in regime.iter_rows(named=True)
            }
            mask = build_regime_filter_mask(
                labels, {"states": ["weak"]}, regime_by_date, required_start=START, required_end=END
            )
            if mask is None:
                raise RuntimeError("R366 weak regime T-1 mask unavailable")
            weak_by_day = {date.fromisoformat(day): bool(value) for day, value in zip(labels, mask, strict=True)}
            eligible = panel.with_columns([
                universe.alias("_universe"),
                pl.col("date").cast(pl.Date).replace_strict(weak_by_day, default=False).alias("_weak"),
                pl.col(FACTOR_ID).is_not_null().alias("_blend_present"),
                pl.col("uf_intraday_reversal").is_not_null().alias("_intraday_present"),
                pl.col("uf_close_position_reversal").is_not_null().alias("_close_present"),
                pl.col("industry_l1").is_not_null().alias("_industry_present"),
                pl.col("log_float_mv").is_not_null().alias("_size_present"),
            ]).filter(pl.col("date").is_between(START, END) & pl.col("_weak") & pl.col("_universe"))
            daily = eligible.group_by("date").agg([
                pl.len().alias("eligible_count"),
                pl.col("_blend_present").mean().alias("blend_coverage"),
                pl.col("_intraday_present").mean().alias("intraday_coverage"),
                pl.col("_close_present").mean().alias("close_coverage"),
                pl.col("_industry_present").mean().alias("industry_coverage"),
                pl.col("_size_present").mean().alias("size_coverage"),
            ]).sort("date")
            if daily.is_empty():
                raise RuntimeError("R366 eligible weak regime panel is empty")
            weak_days = [day for day, enabled in weak_by_day.items() if START <= day <= END and enabled]
            phase_weak = {
                phase.isoformat(): sum(day >= phase for day in weak_days)
                for phase in PHASES
            }
            after = input_fingerprints()
            checks = {
                "D1_paired_scoring_filter_holding_stop_and_neutralize_contract": all(
                    strategies.get(item[key]).max_hold_days == 20
                    and strategies.get(item[key]).stop_loss == -0.20
                    and strategies.get(item[key]).meta["neutralize"] == item["neutralize"]
                    for item in VARIANTS.values() for key in ("candidate", "control")
                ),
                "D2_factor_industry_and_size_columns_materialized": {FACTOR_ID, "uf_intraday_reversal", "uf_close_position_reversal", "industry_l1", "log_float_mv"}.issubset(panel.columns),
                "D3_t1_weak_regime_days_at_least_100": len(weak_days) >= 100,
                "D4_daily_candidate_depth_at_least_10": int(daily["eligible_count"].min()) >= 10,
                "D5_blend_mean_coverage_at_least_95pct": float(daily["blend_coverage"].mean()) >= 0.95,
                "D6_components_and_neutralization_inputs_mean_coverage_at_least_95pct": all(float(daily[name].mean()) >= 0.95 for name in ("intraday_coverage", "close_coverage", "industry_coverage", "size_coverage")),
                "D7_all_six_phase_starts_have_at_least_5_weak_days": all(value >= 5 for value in phase_weak.values()),
                "D8_industry_regime_membership_cover_window": regime["date"].max() >= END and members["effective_date"].max() >= END,
                "D9_source_inputs_byte_stable": before == after,
            }
            payload = {
                "round": "R366",
                "status": "coverage_only_preflight_no_returns",
                "preregistration_sha256": sha(PRE_REG),
                "preflight_runner_sha256": sha(Path(__file__).resolve()),
                "backtest_runner_sha256": sha(EVIDENCE / "run_r366_neutralization.py"),
                "test_sha256": sha(ROOT / "backend/tests/test_r366_neutralization.py"),
                "input_fingerprints_before": before,
                "input_fingerprints_after": after,
                "candidate_ext_columns": sorted(plans["candidate"].ext_columns),
                "panel_rows": panel.height,
                "panel_start": str(panel["date"].min()),
                "panel_end": str(panel["date"].max()),
                "weak_days": len(weak_days),
                "weak_days_by_phase": phase_weak,
                "min_eligible_count": int(daily["eligible_count"].min()),
                "mean_blend_coverage": float(daily["blend_coverage"].mean()),
                "min_blend_coverage": float(daily["blend_coverage"].min()),
                "mean_component_coverage": {
                    "intraday": float(daily["intraday_coverage"].mean()),
                    "close_position": float(daily["close_coverage"].mean()),
                },
                "mean_neutralization_input_coverage": {
                    "industry_l1": float(daily["industry_coverage"].mean()),
                    "log_float_mv": float(daily["size_coverage"].mean()),
                },
                "isolation": isolation_report,
                "checks": checks,
                "all_checks": all(checks.values()),
                "no_return_or_backtest_evaluated": True,
            }
            OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
            print(json.dumps({"all_checks": payload["all_checks"], "checks": checks, "coverage": {key: payload[key] for key in ("weak_days", "min_eligible_count", "mean_blend_coverage", "min_blend_coverage", "mean_neutralization_input_coverage")}, "no_return_or_backtest_evaluated": True}, ensure_ascii=False, indent=2), flush=True)
            return 0 if payload["all_checks"] else 1
    finally:
        settings.data_dir = prior_dir
        regime_builder._REGIME_HISTORY_CACHE, regime_builder._REGIME_HISTORY_SIG = prior_regime_cache
        pipeline.invalidate_custom_signals()
        invalidate_ext_caches(prior_dir, keep_strategy_cache=True)
        load_into_registry(prior_dir)


CANDIDATE_SCORING = {
    "ext_alt_hs300_stock_rel_industry_20d": 0.35,
    "ext_alt_hs300_industry_mom_20d": 0.15,
    "cf_weak_reversal_blend": 0.20,
    "uf_quality_value_defensive": 0.10,
    "uf_cash_quality": 0.20,
}


if __name__ == "__main__":
    raise SystemExit(main())
