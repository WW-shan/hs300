from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
DATA = ROOT / "data"
EVIDENCE = Path(__file__).resolve().parent
R40 = DATA / "research/r40_engine_data"
CACHE = DATA / "research/r360_cache"
R344_CACHE = DATA / "research/r344_cache"
R359_CACHE = DATA / "research/r359_v2_cache"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
DEFENSIVE_ASSETS = DATA / "research/r331_defensive_assets"
PRE_REG = EVIDENCE / "r360_v2_preregistration.json"
PREFLIGHT = EVIDENCE / "r360_v2_preflight.json"
PREFLIGHT_SHA = PREFLIGHT.with_name(PREFLIGHT.name + ".sha256")
RESULT = EVIDENCE / "r360_industry_fade_backtest.json"
RESULT_SHA = RESULT.with_name(RESULT.name + ".sha256")
STATUS = EVIDENCE / "r360_final_status.json"
STATUS_SHA = STATUS.with_name(STATUS.name + ".sha256")
RESULT_MD = EVIDENCE / "r360_result.md"
RESULT_MD_SHA = RESULT_MD.with_name(RESULT_MD.name + ".sha256")
STRATEGY_ID = "ai_hs300_industry_fade_quality_h20_sl20"
CONTROL_STRATEGY_ID = "ai_hs300_industry_relative_strength_quality_h20_sl20"
A_STRATEGY = "ai_hs300_macro_gate_quality"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), RECENT_START, date(2026, 1, 5)]
COSTS = {
    2: {"fees_pct": 0.0004, "commission_pct": 0.0004, "stamp_tax_pct": 0.0005, "slippage_bps": 10.0},
    5: {"fees_pct": 0.0010, "commission_pct": 0.0010, "stamp_tax_pct": 0.00125, "slippage_bps": 25.0},
}
POSITIONS = 10
C_WEIGHT_CAP = 0.10
STOCK_ROUND_TRIP_COST = {2: 0.0010, 5: 0.00325}
OVERLAY_ROUND_TRIP_COST = {2: 0.001, 5: 0.0025}

sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(path: Path, *, follow_symlinks: bool = False) -> dict:
    files = sorted(path.rglob("*"))
    files = [file for file in files if file.is_file() and (follow_symlinks or not file.is_symlink())]
    digest = hashlib.sha256()
    size = 0
    for file in files:
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha256(file).encode())
        digest.update(b"\n")
        size += file.stat().st_size
    return {"files": len(files), "bytes": size, "sha256": digest.hexdigest()}


def input_fingerprints() -> dict:
    baseline_path = EVIDENCE / "r359_v2_cohort_ensemble_backtest.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_prereg_path = EVIDENCE / "r359_v2_preregistration.json"
    baseline_prereg = json.loads(baseline_prereg_path.read_text(encoding="utf-8"))
    r344_hashes = baseline_prereg["frozen_inputs"]["r344_cache_sha256"]
    r344_cache_valid = {
        relative: sha256(ROOT / relative) == expected
        for relative, expected in r344_hashes.items()
    }
    cohort_cache_valid = {}
    for name, info in baseline["cohort_cache_manifest"].items():
        path = R359_CACHE / name
        cohort_cache_valid[name] = (
            path.is_file()
            and path.stat().st_size == int(info["bytes"])
            and sha256(path) == info["sha256"]
        )
    result = {
        "r40_enriched": tree_hash(R40 / "kline_daily_enriched"),
        "raw_daily": tree_hash(DATA / "kline_daily"),
        "production_enriched": tree_hash(DATA / "kline_daily_enriched", follow_symlinks=True),
        "alternative_factors": tree_hash(DATA / "ext_data/alt_hs300/timeseries"),
        "alternative_config": sha256(DATA / "ext_data/alt_hs300/config.json"),
        "industry_snapshot": sha256(DATA / "ext_data/ext_hy_ths/part.parquet"),
        "membership": sha256(MEMBERSHIP),
        "financials": {
            path.relative_to(DATA).as_posix(): sha256(path)
            for path in sorted((DATA / "financials").glob("*/part.parquet"))
        },
        "instruments": sha256(DATA / "instruments/instruments.parquet"),
        "custom_factors": tree_hash(DATA / "user_data/custom_factors"),
        "custom_signals": tree_hash(DATA / "user_data/custom_signals"),
        "defensive_assets": {
            path.name: sha256(path)
            for path in sorted(DEFENSIVE_ASSETS.glob("*.parquet"))
        },
        "r359_result": sha256(baseline_path),
        "r359_preregistration": sha256(baseline_prereg_path),
        "r344_cache_valid": all(r344_cache_valid.values()),
        "r344_cache_invalid": [name for name, valid in r344_cache_valid.items() if not valid],
        "r359_cohort_cache_valid": all(cohort_cache_valid.values()),
        "r359_cohort_cache_invalid": [name for name, valid in cohort_cache_valid.items() if not valid],
        "r359_cohort_cache_manifest_count": len(cohort_cache_valid),
    }
    return result


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
    from app.backtest import strategy as strategy_module

    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: frame
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty backtest curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def stats(curve: pl.DataFrame) -> dict:
    values = [float(value) for value in curve["value"].to_list()]
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    annual = (1.0 + total) ** (252.0 / len(returns)) - 1.0
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(252.0) if sd else 0.0
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {"total_return": total, "annual": annual, "sharpe": sharpe, "mdd": mdd, "days": len(values)}


def segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float:
    segment = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if segment.height < 2:
        raise RuntimeError(f"missing curve segment {start}..{end}")
    return float(segment["value"][-1] / segment["value"][0] - 1.0)


def year_returns(curve: pl.DataFrame) -> dict[str, float]:
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r360_year_returns")
    return r315.year_returns(curve)


def combine_three(curves: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame], r112, r344, cost: float):
    risk, weights = r344._combine_three(curves, cost, C_WEIGHT_CAP)
    equity = r112._stats(risk)
    return risk, weights, equity


def load_cached_curve(strategy_id: str, start: date, factor: int, positions: int = POSITIONS) -> pl.DataFrame:
    path = R344_CACHE / f"{strategy_id}_{start}_{factor}x_p{positions}.parquet"
    if not path.exists():
        raise RuntimeError(f"baseline cache missing: {path}")
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def validate_preflight_manifest(
    preflight: dict,
    prereg: dict,
    *,
    preregistration_sha256: str,
    preflight_sha256: str,
    checksum_sha256: str,
) -> None:
    frozen = prereg["frozen_inputs"]
    checks = {
        "all_pass": preflight.get("all_pass") is True,
        "checksum": preflight_sha256 == checksum_sha256,
        "preregistration": preflight.get("preregistration_sha256") == preregistration_sha256,
        "backtest runner": preflight.get("backtest_runner_sha256") == frozen["runner_sha256"],
        "preflight runner": preflight.get("preflight_runner_sha256") == frozen["preflight_runner_sha256"],
        "test": preflight.get("test_sha256") == frozen["test_sha256"],
        "isolation helper": preflight.get("isolation_helper_sha256") == frozen["isolation_helper_sha256"],
    }
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise RuntimeError(f"R360 preflight binding failed: {', '.join(failures)}")


def run() -> int:
    prereg = json.loads(PRE_REG.read_text(encoding="utf-8"))
    baseline = json.loads((EVIDENCE / "r359_v2_cohort_ensemble_backtest.json").read_text(encoding="utf-8"))
    base_sha = sha256(EVIDENCE / "r359_v2_cohort_ensemble_backtest.json")
    if base_sha != prereg["frozen_inputs"]["baseline_result_sha256"]:
        raise RuntimeError("R359 baseline artifact changed after R360 preregistration")
    if input_fingerprints() != prereg["frozen_inputs"]["data_fingerprints"]:
        raise RuntimeError("R360 input fingerprints differ from preregistration")
    for relative, expected in prereg["frozen_inputs"]["code_sha256"].items():
        if sha256(ROOT / relative) != expected:
            raise RuntimeError(f"R360 frozen code input changed: {relative}")
    if sha256(EVIDENCE / "run_r360_industry_fade.py") != prereg["frozen_inputs"]["runner_sha256"]:
        raise RuntimeError("R360 runner changed after preregistration")
    if sha256(EVIDENCE / "run_r360_preflight.py") != prereg["frozen_inputs"]["preflight_runner_sha256"]:
        raise RuntimeError("R360 preflight runner changed after preregistration")
    if sha256(ROOT / "backend/tests/test_r360_industry_fade.py") != prereg["frozen_inputs"]["test_sha256"]:
        raise RuntimeError("R360 accounting/signal test changed after preregistration")
    if sha256(EVIDENCE / "r360_strategies/ai_hs300_industry_fade_quality_h20_sl20.py") != prereg["frozen_inputs"]["candidate_strategy_sha256"]:
        raise RuntimeError("R360 strategy changed after preregistration")
    if sha256(DATA / "user_data/custom_factors/uf_industry_relative_strength_change_5d.json") != prereg["frozen_inputs"]["factor_definition_sha256"]:
        raise RuntimeError("R360 factor changed after preregistration")
    if sha256(DATA / "user_data/custom_signals/r360_rel_strength_fade.json") != prereg["frozen_inputs"]["exit_signal_sha256"]:
        raise RuntimeError("R360 exit signal changed after preregistration")
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    expected_preflight_sha = PREFLIGHT_SHA.read_text(encoding="ascii").split()[0]
    validate_preflight_manifest(
        preflight,
        prereg,
        preregistration_sha256=sha256(PRE_REG),
        preflight_sha256=sha256(PREFLIGHT),
        checksum_sha256=expected_preflight_sha,
    )
    if RESULT.exists() or RESULT_SHA.exists() or STATUS.exists() or RESULT_MD.exists():
        raise RuntimeError("R360 output exists; refusing overwrite")

    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r360")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r360")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r360")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r360")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r360")
    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r360")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r344_r360")
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r359_r360")
    r360_isolation = module(EVIDENCE / "r360_isolation.py", "r360_isolation_runner")
    r114.TARGET_VOL = 0.15
    r114.ROUND_TRIP_COST = 0.0052

    # Isolated engine data root; source histories stay read-only.
    work = Path(tempfile.mkdtemp(prefix="r360_engine_data_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        isolation_report = r360_isolation.prepare_isolated_data_root(engine_root, DATA)
        if not isolation_report["isolated"] or isolation_report["alternative_timeseries_is_symlink"]:
            raise RuntimeError("R360 factors and signals are not isolated from live data")
        from app.config import settings

        settings.data_dir = engine_root
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", EVIDENCE / "r360_strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        strategy = engine.get(STRATEGY_ID)
        if sha256(EVIDENCE / "r360_strategies/ai_hs300_industry_fade_quality_h20_sl20.py") != preflight["strategy_sha256"]:
            raise RuntimeError("R360 candidate strategy changed after preflight")
        if strategy.max_hold_days != 20 or strategy.stop_loss != -0.20:
            raise RuntimeError("R360 frozen exit horizon/stop changed")
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), engine
        )
        if service.engine.repo.store.data_dir != engine_root:
            raise RuntimeError("R360 strategy engine does not use the isolated data root")
        members = membership()
        CACHE.mkdir(parents=True, exist_ok=True)

        def run_candidate(start: date, factor: int, *, capture: bool = False):
            key = f"{start}_{factor}x"
            curve_path = CACHE / f"{key}.parquet"
            trades_path = CACHE / f"{key}.trades.json"
            if curve_path.exists() or trades_path.exists():
                raise RuntimeError(f"R360 candidate cache already exists: {key}")
            cfg = StrategyBacktestConfig(
                strategy_id=STRATEGY_ID,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                max_positions=POSITIONS,
                max_exposure_pct=1.0,
                initial_capital=1_000_000.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
                **COSTS[factor],
            )
            started = time.perf_counter()
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"R360 {start} {factor}x: {result.error}")
            curve = curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != END:
                raise RuntimeError(f"R360 date coverage mismatch {key}")
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture:
                trades = [
                    {
                        "symbol": trade.get("symbol"),
                        "entry_date": str(trade.get("entry_date")),
                        "exit_date": str(trade.get("exit_date")),
                        "pnl_pct": float(trade.get("pnl_pct") or 0.0),
                        "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                        "exit_reason": trade.get("exit_reason"),
                    }
                    for trade in result.trades
                ]
                trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            print(f"R360 ran {key}: days={curve.height} trades={len(result.trades)} elapsed={time.perf_counter()-started:.1f}s", flush=True)
            return curve, trades, result.stats

        controls: dict[tuple[date, int], pl.DataFrame] = {}
        candidates: dict[tuple[date, int], pl.DataFrame] = {}
        candidate_trades: dict[int, list[dict]] = {}
        candidate_standalone_stats: dict[tuple[date, int], dict] = {}
        sessions = pl.read_parquet(
            R344_CACHE / f"{C_STRATEGY}_2022-05-05_2x_p50.parquet",
            columns=["date"],
        )["date"].cast(pl.Date).to_list()

        def load_c_cohort_curve(start: date, factor: int) -> pl.DataFrame:
            path = R359_CACHE / f"{C_STRATEGY}_{start}_{factor}x_p50.parquet"
            if not path.exists():
                raise RuntimeError(f"R359 frozen C cohort cache missing: {path}")
            return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")

        def load_c_ensemble_curve(phase: date, factor: int) -> pl.DataFrame:
            starts = r359.cohort_start_dates(sessions, phase)
            curves = [load_c_cohort_curve(start, factor) for start in starts]
            return r359.combine_cohort_curves(curves, starts)

        for factor in (2, 5):
            candidates[(START, factor)], trades, standalone_stats = run_candidate(START, factor, capture=True)
            candidate_trades[factor] = trades
            candidate_standalone_stats[(START, factor)] = standalone_stats
            controls[(START, factor)] = load_cached_curve(CONTROL_STRATEGY_ID, START, factor)
            control_trade_path = R344_CACHE / f"{CONTROL_STRATEGY_ID}_{START}_{factor}x_p10.trades.json"
            if not control_trade_path.exists():
                raise RuntimeError(f"R360 frozen B control trades missing: {control_trade_path}")
        for phase in PHASES:
            if phase == START:
                continue
            candidates[(phase, 2)], _, standalone_stats = run_candidate(phase, 2)
            candidate_standalone_stats[(phase, 2)] = standalone_stats
            controls[(phase, 2)] = load_cached_curve(CONTROL_STRATEGY_ID, phase, 2)

        # A/C sleeves and asset data are frozen; reuse validated R344/R359 cache outputs.
        outer: dict[tuple[str, date, int], dict] = {}
        outer_risk: dict[tuple[str, date, int], pl.DataFrame] = {}
        outer_sleeve_weights: dict[tuple[str, date, int], list[dict]] = {}
        for phase in PHASES:
            for factor in ((2, 5) if phase == START else (2,)):
                a = load_cached_curve(A_STRATEGY, phase, factor)
                c = load_c_ensemble_curve(phase, factor)
                equity = []
                for label, b in (("control", controls[(phase, factor)]), ("candidate", candidates[(phase, factor)])):
                    risk_curve, sleeve_weights = r344._combine_three((a, b, c), r112.ROUND_TRIP_COST, C_WEIGHT_CAP)
                    bucket = r114._vol_target(risk_curve)["curve"]
                    gold = r331.asset_frame("gold", "518880.SH")
                    treasury = r331.asset_frame("treasury", "511260.SH")
                    dividend = r331.asset_frame("dividend", "510880.SH")
                    old_lookback, old_target, old_cap = r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX
                    try:
                        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = 60, 0.15, 1.0 / 3.0
                        gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, OVERLAY_ROUND_TRIP_COST[factor])
                        final = r331.build_three_asset_bucket(
                            bucket,
                            gold_proxy,
                            treasury,
                            dividend,
                            round_trip_cost=OVERLAY_ROUND_TRIP_COST[factor],
                        )
                    finally:
                        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_lookback, old_target, old_cap
                    equity.append((label, final, sleeve_weights, risk_curve))
                    outer[(label, phase, factor)] = final
                    outer_risk[(label, phase, factor)] = risk_curve
                    outer_sleeve_weights[(label, phase, factor)] = sleeve_weights

        # Statistics and fixed pre-registered gates.
        main2 = {label: outer[(label, START, 2)]["curve"] for label in ("control", "candidate")}
        main5 = {label: outer[(label, START, 5)]["curve"] for label in ("control", "candidate")}
        phase_metrics = {}
        for phase in PHASES:
            for label in ("control", "candidate"):
                c = outer[(label, phase, 2)]["curve"]
                phase_metrics.setdefault(label, {})[str(phase)] = {
                    "stats": stats(c),
                    "yearly": year_returns(c),
                    "recent": segment_return(c, RECENT_START),
                    "segment_2022": segment_return(c, START, date(2022, 12, 31)) if phase == START else None,
                }
        perf = {label: {
            "2x": stats(main2[label]),
            "5x": stats(main5[label]),
            "yearly_2x": year_returns(main2[label]),
            "recent_2x": segment_return(main2[label], RECENT_START),
            "segment_2022_2x": segment_return(main2[label], START, date(2022, 12, 31)),
        } for label in ("control", "candidate")}

        industry = r313.load_industry_l1(engine_root)
        industry_map = {str(row["symbol"]): str(row["industry_l1"]) for row in industry.iter_rows(named=True) if row["industry_l1"]}
        conc = {}
        per_label_trades = {}
        for label in ("control", "candidate"):
            a_trades = json.loads((R344_CACHE / f"{A_STRATEGY}_{START}_5x_p10.trades.json").read_text(encoding="utf-8"))
            if label == "candidate":
                b_trades = [{**trade, "sleeve": "B"} for trade in candidate_trades[5]]
            else:
                b_trades = json.loads((R344_CACHE / f"{CONTROL_STRATEGY_ID}_{START}_5x_p10.trades.json").read_text(encoding="utf-8"))
            c_trades = []
            for cohort_id, cohort_start in enumerate(r359.cohort_start_dates(sessions, START)):
                c_path = R359_CACHE / f"{C_STRATEGY}_{cohort_start}_5x_p50.trades.json"
                c_trades.extend(r359.annotate_cohort_trades(json.loads(c_path.read_text(encoding="utf-8")), cohort_id))
            per_label_trades[label] = [*a_trades, *b_trades, *c_trades]
        for label in ("control", "candidate"):
            factor = 5
            trades = per_label_trades[label]
            core = outer[(label, START, factor)]
            risk_curve = outer_risk[(label, START, factor)]
            records = core["rebalances"]
            sleeve_curve_weights = outer_sleeve_weights[(label, START, factor)]
            scaled = []
            for trade in trades:
                entry = r326.parse_day(trade["entry_date"])
                exit_day = r326.parse_day(trade["exit_date"]) if trade.get("exit_date") else END
                days = [day for day in risk_curve["date"].to_list() if entry <= day <= exit_day] or [entry]
                scales = []
                for day in days:
                    ew = r326.weight_at(records, day, "target_equity_weight")
                    sleeve = str(trade.get("sleeve", "B")).lower()
                    sw = r326.weight_at(sleeve_curve_weights, day, f"weight_{sleeve}")
                    scales.append(ew * sw)
                row = {**trade, "pnl_amount": float(trade["pnl_amount"]) * sum(scales) / len(scales)}
                scaled.append(row)
            conc[label] = r239._aggregate(scaled, industry_map)

        trade_diagnostics = {}
        for label, trades in per_label_trades.items():
            b_trades = [trade for trade in trades if trade.get("sleeve") == "B"]
            reasons = {}
            for trade in b_trades:
                reasons[trade.get("exit_reason", "unknown")] = reasons.get(trade.get("exit_reason", "unknown"), 0) + 1
            trade_diagnostics[label] = {
                "portfolio_stock_trades": len(trades),
                "b_sleeve_trades": len(b_trades),
                "exit_reasons": reasons,
                "early_signal_exits_under_5_days": sum(1 for trade in b_trades if trade.get("exit_reason") == "signal" and (r326.parse_day(trade["exit_date"]) - r326.parse_day(trade["entry_date"])).days <= 5),
            }

        max_weight_sum = max(
            float(outer[(label, phase, factor)]["weight_summary"]["max_weight_sum"])
            for label in ("control", "candidate")
            for phase, factor in ((START, 2), (START, 5))
        )
        min_target_weight = min(
            float(outer[(label, phase, factor)]["weight_summary"]["min_target_weight"])
            for label in ("control", "candidate")
            for phase, factor in ((START, 2), (START, 5))
        )

        gate = {
            "G1_2x_annual_not_below_R359_by_more_than_1pp": perf["candidate"]["2x"]["annual"] >= baseline["candidate"]["performance"]["2"]["stats"]["annual"] - 0.01,
            "G2_2x_sharpe_not_below_R359_by_more_than_0_10": perf["candidate"]["2x"]["sharpe"] >= baseline["candidate"]["performance"]["2"]["stats"]["sharpe"] - 0.10,
            "G3_2x_mdd_not_deeper_than_R359": perf["candidate"]["2x"]["mdd"] >= baseline["candidate"]["performance"]["2"]["stats"]["mdd"],
            "G4_2022_segment_nonnegative": perf["candidate"]["segment_2022_2x"] >= 0,
            "G5_recent_year_positive": perf["candidate"]["recent_2x"] > 0,
            "G6_minimum_4_of_6_fresh_starts_positive": sum(phase_metrics["candidate"][str(phase)]["stats"]["total_return"] > 0 for phase in PHASES) >= 4,
            "G7_5x_annual_at_least_10pct": perf["candidate"]["5x"]["annual"] >= 0.10,
            "G8_no_leverage": max_weight_sum <= 1.0 + 1e-12 and min_target_weight >= 0.0,
        }
        input_after = {
            **input_fingerprints(),
        }
        if input_after != preflight["input_fingerprints_before"]:
            raise RuntimeError("R360 frozen input fingerprints changed during backtest")
        result = {
            "round": "R360",
            "classification": "single pre-registered fixed-exit-rule test; research-only",
            "hypothesis": prereg["hypothesis"],
            "rule": prereg["rule"],
            "window": prereg["primary_window"],
            "preflight_sha256": sha256(PREFLIGHT),
            "preregistration_sha256": sha256(PRE_REG),
            "baseline_r359_sha256": base_sha,
            "performance": perf,
            "phase_metrics": phase_metrics,
            "trade_diagnostics": trade_diagnostics,
            "concentration_5x": conc,
            "standalone_b_stats": {
                str(phase): {
                    str(factor): {
                        "candidate": candidate_standalone_stats[(phase, factor)],
                        "control": stats(controls[(phase, factor)]),
                    }
                    for factor in ((2, 5) if phase == START else (2,))
                }
                for phase in PHASES
            },
            "portfolio_weights": {"max_weight_sum": max_weight_sum, "min_target_weight": min_target_weight},
            "gates": gate,
            "all_gates_pass": all(gate.values()),
            "data_integrity": {"stable": True, "input_fingerprints_after": input_after},
            "isolation": isolation_report,
            "decision": {"selected": None, "paper_target_generated": False, "orders_generated": False, "production_enabled": False, "no_leverage": True},
            "limits": prereg["known_limits"],
        }
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        RESULT_SHA.write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
        status = {
            "round": "R360",
            "status": "research_only_candidate_for_next_validation" if all(gate.values()) else "research_only_no_candidate_after_r360_industry_fade",
            "all_gates_pass": all(gate.values()),
            "failed_gates": [key for key, value in gate.items() if not value],
            "selected": None,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
            "no_leverage": True,
            "result": RESULT.name,
            "result_sha256": sha256(RESULT),
        }
        STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        STATUS_SHA.write_text(f"{sha256(STATUS)}  {STATUS.name}\n", encoding="ascii")
        note = "\n".join([
            "# R360 · 行业相对强弱转负/走弱退出",
            "",
            "- 固定信号：当前行业相对 20 日收益 < 0，且与 5 个交易日前相比继续走弱；T 日收盘生成，T+1 开盘退出。原 B 入场排序、20 日上限、-20% 止损和外层组合冻结。",
            f"- B 单袖 2x：控制年化 {perf['control']['2x']['annual']:.2%} / Sharpe {perf['control']['2x']['sharpe']:.3f} / MDD {perf['control']['2x']['mdd']:.2%}；候选 {perf['candidate']['2x']['annual']:.2%} / {perf['candidate']['2x']['sharpe']:.3f} / {perf['candidate']['2x']['mdd']:.2%}。",
            f"- 组合 2x：控制年化 {perf['control']['2x']['annual']:.2%}，近一年 {perf['control']['recent_2x']:.2%}；候选年化 {perf['candidate']['2x']['annual']:.2%}，近一年 {perf['candidate']['recent_2x']:.2%}，2022 段 {perf['candidate']['segment_2022_2x']:.2%}。",
            f"- 组合 5x 年化：控制 {perf['control']['5x']['annual']:.2%}；候选 {perf['candidate']['5x']['annual']:.2%}。通过 {sum(gate.values())}/{len(gate)} 门槛，selected=null。",
            "- 非 PIT 行业分类回填、历史成分偏差和重叠样本限制保留；无杠杆、无纸面目标、无订单、未启用生产。",
            "",
            "证据：`r360_industry_fade_backtest.json`、`r360_final_status.json`。",
            "",
        ])
        RESULT_MD.write_text(note, encoding="utf-8")
        RESULT_MD_SHA.write_text(f"{sha256(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
        print(json.dumps({"status": status, "performance": perf, "gates": gate}, ensure_ascii=False, indent=2), flush=True)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(run())
