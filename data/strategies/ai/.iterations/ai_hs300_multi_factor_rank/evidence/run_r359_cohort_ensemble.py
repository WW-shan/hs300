from __future__ import annotations

import atexit
import hashlib
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research" / "r40_engine_data"
STOCK_CACHE = DATA / "research" / "r344_cache"
RUN_CACHE = DATA / "research" / "r359_v2_cache"
PREREG = EVIDENCE / "r359_v2_preregistration.json"
PREFLIGHT = EVIDENCE / "r359_v2_preflight.json"
PREFLIGHT_AMENDMENT = EVIDENCE / "r359_v2_preflight_amendment.json"
OUTPUT = EVIDENCE / "r359_v2_cohort_ensemble_backtest.json"
STATUS = EVIDENCE / "r359_v2_final_status.json"
RESULT_MD = EVIDENCE / "r359_v2_result.md"
ERRATUM = EVIDENCE / "r359_execution_scale_erratum_A1.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
STRATEGIES = (A_STRATEGY, B_STRATEGY, C_STRATEGY)
SLEEVES = {A_STRATEGY: "A", B_STRATEGY: "B", C_STRATEGY: "C"}
POSITIONS = {A_STRATEGY: 10, B_STRATEGY: 10, C_STRATEGY: 50}
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
PHASES = [START, date(2023, 1, 3), date(2024, 1, 2), date(2025, 1, 2), RECENT_START, date(2026, 1, 5)]
COHORT_OFFSETS = (0, 20, 40)
C_WEIGHT_CAP = 0.10
OVERLAY_COST = {2: 0.001, 3: 0.0015, "3c": 0.003, 4: 0.002, 5: 0.0025}
STOCK_COST = {
    2: {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
    3: {"fees": 0.0006, "commission": 0.0006, "stamp": 0.001, "slippage": 15.0},
    4: {"fees": 0.0008, "commission": 0.0008, "stamp": 0.001, "slippage": 20.0},
    5: {"fees": 0.0010, "commission": 0.0010, "stamp": 0.00125, "slippage": 25.0},
}

sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def cohort_start_dates(sessions: list[date], anchor: date) -> list[date]:
    try:
        anchor_index = sessions.index(anchor)
    except ValueError as exc:
        raise ValueError(f"cohort anchor is not a trading session: {anchor}") from exc
    starts = []
    for offset in COHORT_OFFSETS:
        index = anchor_index + offset
        if index >= len(sessions):
            raise ValueError(f"cohort start offset {offset} exceeds available sessions")
        starts.append(sessions[index])
    return starts


def combine_cohort_curves(curves: list[pl.DataFrame], starts: list[date]) -> pl.DataFrame:
    if len(curves) != 3 or len(starts) != 3:
        raise ValueError("R359 requires exactly three cohort curves and starts")
    base_dates = curves[0]["date"].to_list()
    if not base_dates or base_dates[0] != starts[0]:
        raise ValueError("first cohort must start on the portfolio anchor")
    navs: list[list[float]] = []
    start_indices: list[int] = []
    for curve, start in zip(curves, starts, strict=True):
        dates = curve["date"].to_list()
        values = curve["value"].to_list()
        if not dates or dates[0] != start or dates[-1] != base_dates[-1]:
            raise ValueError("cohort curve has an unexpected start or end date")
        start_index = base_dates.index(start) if start in base_dates else -1
        if start_index < 0 or dates != base_dates[start_index:]:
            raise ValueError("daily calendar mismatch between cohort curves")
        initial = float(values[0])
        if not math.isfinite(initial) or initial <= 0:
            raise ValueError("cohort initial value must be finite and positive")
        navs.append([float(value) / initial for value in values])
        start_indices.append(start_index)

    aggregate = []
    for day_index in range(len(base_dates)):
        account_values = [
            (navs[cohort_index][day_index - start_indices[cohort_index]]
             if day_index >= start_indices[cohort_index] else 1.0)
            / 3.0
            for cohort_index in range(3)
        ]
        aggregate.append(sum(account_values))
    return pl.DataFrame({"date": base_dates, "value": aggregate})


def annotate_cohort_trades(trades: list[dict], cohort_id: int) -> list[dict]:
    if cohort_id not in (0, 1, 2):
        raise ValueError("cohort_id must be 0, 1 or 2")
    return [
        {
            **trade,
            "cohort_id": cohort_id,
            "cohort_initial_weight": 1.0 / 3.0,
            "pnl_amount": float(trade.get("pnl_amount") or 0.0),
        }
        for trade in trades
    ]


def segment_return(curve: pl.DataFrame, start: date, end: date = END) -> float | None:
    segment = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if segment.height < 2:
        return None
    values = segment["value"].to_list()
    return values[-1] / values[0] - 1.0


def main() -> int:
    if any(path.exists() for path in (OUTPUT, STATUS, RESULT_MD)):
        raise RuntimeError("R359 output already exists; refusing overwrite")
    for path in (PREREG, PREFLIGHT, PREFLIGHT_AMENDMENT, ERRATUM):
        sidecar = path.with_name(path.name + ".sha256")
        if not sidecar.exists() or sha256(path) != sidecar.read_text(encoding="ascii").split()[0]:
            raise RuntimeError(f"R359 checksum mismatch: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    preflight = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    if prereg.get("round") != "R359" or prereg.get("status") != "post_hoc_execution_scale_corrected_v2_before_v2_replay":
        raise RuntimeError("R359 preregistration status is invalid")
    if not preflight.get("all_checks") or not preflight.get("no_return_or_backtest_evaluated"):
        raise RuntimeError("R359 preflight did not pass")
    if preflight.get("preregistration_sha256") != sha256(PREREG):
        raise RuntimeError("R359 preflight is not bound to the current preregistration")
    if preflight.get("runner_sha256") != sha256(EVIDENCE / "run_r359_cohort_preflight.py"):
        raise RuntimeError("R359 preflight runner hash does not match current source")
    if preflight.get("cohort_runner_sha256") != sha256(Path(__file__).resolve()):
        raise RuntimeError("R359 cohort runner hash does not match preflight")
    erratum = json.loads(ERRATUM.read_text(encoding="utf-8"))
    if erratum.get("status") != "execution_scale_correction_before_v2_replay" or erratum.get("returns_already_observed") is not True:
        raise RuntimeError("R359 execution scale erratum is invalid")
    amendment = json.loads(PREFLIGHT_AMENDMENT.read_text(encoding="utf-8"))
    if amendment.get("status") != "post_hoc_execution_scale_correction_before_v2_replay" or amendment.get("returns_already_observed") is not True:
        raise RuntimeError("R359 v2 execution-scale amendment is invalid")
    frozen = prereg["frozen_inputs"]
    if sha256(Path(__file__).resolve()) != frozen["cohort_runner_sha256"]:
        raise RuntimeError("R359 backtest runner changed after preregistration")
    if sha256(EVIDENCE / "run_r359_cohort_preflight.py") != frozen["preflight_runner_sha256"]:
        raise RuntimeError("R359 preflight runner changed after preregistration")
    if preflight.get("preregistration_sha256") != sha256(PREREG):
        raise RuntimeError("R359 preflight does not match frozen preregistration")
    if preflight.get("runner_sha256") != frozen["preflight_runner_sha256"]:
        raise RuntimeError("R359 preflight runner hash does not match preregistration")
    if preflight.get("cohort_runner_sha256") != frozen["cohort_runner_sha256"]:
        raise RuntimeError("R359 cohort runner hash does not match preregistration")
    strategy_path = DATA / "strategies" / "ai" / f"{C_STRATEGY}.py"
    if sha256(strategy_path) != prereg["frozen_inputs"]["C_strategy_sha256"]:
        raise RuntimeError("R359 C strategy changed after preregistration")
    for relative_path, expected in frozen["engine_code_sha256"].items():
        if sha256(ROOT / relative_path) != expected:
            raise RuntimeError(f"R359 engine code changed after preregistration: {relative_path}")
    for relative_path, expected in frozen["r344_cache_sha256"].items():
        if sha256(ROOT / relative_path) != expected:
            raise RuntimeError(f"R359 R344 stock cache changed after preregistration: {relative_path}")
    preflight_module = module(EVIDENCE / "run_r359_cohort_preflight.py", "r359_preflight_for_backtest")
    before_inputs = preflight_module.fingerprints()
    if before_inputs != frozen["data_fingerprints"]:
        raise RuntimeError("R359 input fingerprints changed after preflight")
    if before_inputs != preflight.get("data_fingerprints"):
        raise RuntimeError("R359 preflight data fingerprints do not match frozen inputs")
    if sha256(ROOT / "backend/tests/test_r359_cohort_ensemble.py") != frozen.get("test_sha256"):
        raise RuntimeError("R359 cohort accounting test changed after preregistration")

    r331 = module(EVIDENCE / "run_r331_three_asset_bucket.py", "r331_r359")
    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r359")
    r114 = module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r114_r359")
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r239_r359")
    r313 = module(EVIDENCE / "run_r313_concentration_audit.py", "r313_r359")
    r315 = module(EVIDENCE / "run_r315_holding_bootstrap.py", "r315_r359")
    r324 = module(EVIDENCE / "run_r324_defensive_ballast.py", "r324_r359")
    r326 = module(EVIDENCE / "run_r326_gold_concentration.py", "r326_r359")
    r344 = module(EVIDENCE / "run_r344_extended_window.py", "r344_r359")
    r331.self_test()
    members = r313.membership()
    before_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    before_prod = r331.tree_hash(DATA / "kline_daily_enriched")

    calendar_path = STOCK_CACHE / f"{C_STRATEGY}_{START}_2x_p50.parquet"
    sessions = pl.read_parquet(calendar_path, columns=["date"]).with_columns(pl.col("date").cast(pl.Date))["date"].to_list()
    engine = r324.StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: r324.strategy_config.load_override(DATA, sid),
        industry_loader=r324.industry_loader_for(DATA),
    )
    for sleeve, strategy_id in zip(("A", "B", "C"), STRATEGIES, strict=True):
        strategy = engine.get(strategy_id)
        if sha256(strategy.file_path) != prereg["frozen_inputs"][f"{sleeve}_strategy_sha256"]:
            raise RuntimeError(f"R359 {sleeve} strategy changed after preregistration")
    if engine.get(A_STRATEGY).max_hold_days != 35 or engine.get(B_STRATEGY).max_hold_days != 20 or engine.get(C_STRATEGY).max_hold_days != 60:
        raise RuntimeError("R359 frozen sleeve holding periods changed")
    engine_work = Path(tempfile.mkdtemp(prefix="r359_engine_data_"))
    atexit.register(shutil.rmtree, engine_work, ignore_errors=True)
    engine_root = engine_work / "engine_data"
    shutil.copytree(R40, engine_root, symlinks=True)
    r40_latest = r331.latest_partition_date(R40 / "kline_daily_enriched")
    if r40_latest is None:
        raise RuntimeError("R359 R40 enriched history is empty")
    for source in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet")):
        partition_day = date.fromisoformat(source.parent.name.split("=")[1])
        if partition_day <= r40_latest:
            continue
        target = engine_root / "kline_daily_enriched" / source.parent.name / "part.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    raw_target = engine_root / "kline_daily"
    if raw_target.is_symlink() or raw_target.is_file():
        raw_target.unlink()
    elif raw_target.exists():
        shutil.rmtree(raw_target)
    shutil.copytree(DATA / "kline_daily", raw_target)
    r324.load_into_registry(DATA)
    engine = r324.StrategyEngine(
        strategy_dirs=[DATA / "strategies/ai"],
        override_loader=lambda sid: r324.strategy_config.load_override(DATA, sid),
        industry_loader=r324.industry_loader_for(DATA),
    )
    service = r324.StrategyBacktestService(r324.BacktestEngine(r324.KlineRepository(r324.DataStore(engine_root))), engine)
    RUN_CACHE.mkdir(parents=True, exist_ok=True)

    def run_c(start: date, factor: int, *, capture_trades: bool) -> tuple[pl.DataFrame, list[dict]]:
        key = f"{C_STRATEGY}_{start}_{factor}x_p50"
        curve_path = RUN_CACHE / f"{key}.parquet"
        trades_path = RUN_CACHE / f"{key}.trades.json"
        meta_path = RUN_CACHE / f"{key}.meta.json"
        expected_meta = {
            "strategy_sha256": prereg["frozen_inputs"]["C_strategy_sha256"],
            "preregistration_sha256": sha256(PREREG),
            "membership_sha256": frozen["data_fingerprints"]["membership"],
            "input_fingerprints_sha256": hashlib.sha256(
                json.dumps(frozen["data_fingerprints"], sort_keys=True).encode()
            ).hexdigest(),
            "start": str(start),
            "end": str(END),
            "factor": factor,
            "fees_pct": STOCK_COST[factor]["fees"],
            "commission_pct": STOCK_COST[factor]["commission"],
            "stamp_tax_pct": STOCK_COST[factor]["stamp"],
            "slippage_bps": STOCK_COST[factor]["slippage"],
            "matching": "open_t+1",
            "initial_capital": 1_000_000.0 / 3.0,
            "max_positions": 50,
            "max_exposure_pct": 1.0,
            "position_sizing": "equal",
            "asset_type": "stock",
            "hs300": True,
        }
        any_cache_piece = any(path.exists() for path in (curve_path, trades_path, meta_path))
        if any_cache_piece and not (curve_path.exists() and meta_path.exists()):
            raise RuntimeError(f"R359 incomplete C cache entry: {key}")
        if curve_path.exists() and meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if any(meta.get(key) != value for key, value in expected_meta.items()):
                raise RuntimeError(f"R359 cache metadata mismatch: {key}")
            if meta.get("curve_sha256") != sha256(curve_path):
                raise RuntimeError(f"R359 cohort curve cache checksum mismatch: {key}")
            expected_trades_hash = meta.get("trades_sha256")
            if expected_trades_hash is not None and (not trades_path.exists() or sha256(trades_path) != expected_trades_hash):
                raise RuntimeError(f"R359 cohort trade cache checksum mismatch: {key}")
            curve = pl.read_parquet(curve_path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            trades = json.loads(trades_path.read_text(encoding="utf-8")) if capture_trades else []
            if capture_trades and not trades_path.exists():
                raise RuntimeError(f"R359 trade cache missing: {key}")
        else:
            params = STOCK_COST[factor]
            cfg = r324.StrategyBacktestConfig(
                strategy_id=C_STRATEGY,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides={},
                matching="open_t+1",
                fees_pct=params["fees"],
                commission_pct=params["commission"],
                stamp_tax_pct=params["stamp"],
                slippage_bps=params["slippage"],
                initial_capital=1_000_000.0 / 3.0,
                max_positions=50,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            started = time.perf_counter()
            with r324.patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"R359 C {start} {factor}x: {result.error}")
            curve = r324.curve_frame(result.equity_curve)
            curve.write_parquet(curve_path, compression="zstd")
            trades = []
            if capture_trades:
                trades = [{
                    "sleeve": "C",
                    "symbol": trade.get("symbol"),
                    "name": trade.get("name"),
                    "entry_date": str(trade.get("entry_date")),
                    "exit_date": str(trade.get("exit_date")),
                    "pnl_pct": trade.get("pnl_pct"),
                    "pnl_amount": float(trade.get("pnl_amount") or 0.0),
                    "exit_reason": trade.get("exit_reason"),
                } for trade in result.trades]
                trades_path.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
            expected_meta["curve_sha256"] = sha256(curve_path)
            expected_meta["trades_sha256"] = sha256(trades_path) if capture_trades else None
            meta_path.write_text(json.dumps(expected_meta, sort_keys=True), encoding="utf-8")
            print(f"ran C {start} {factor}x: days={curve.height} elapsed={time.perf_counter() - started:.1f}s", flush=True)
        if curve["date"].min() != start or curve["date"].max() != END:
            raise RuntimeError(f"R359 C cohort has bad date coverage: {key}")
        if any(curve["date"][index] >= curve["date"][index + 1] for index in range(curve.height - 1)):
            raise RuntimeError(f"R359 C cohort dates are not strictly increasing: {key}")
        if not curve["value"].is_finite().all() or curve["value"].min() <= 0:
            raise RuntimeError(f"R359 C cohort NAV contains invalid values: {key}")
        return curve, trades

    cohort_curves: dict[str, dict[int, pl.DataFrame]] = {}
    cohort_trades: dict[int, list[dict]] = {}
    all_runs: dict[str, list[dict]] = {}
    for factor in (2, 3, 4, 5):
        starts = cohort_start_dates(sessions, START)
        curves, trades = [], []
        for cohort_id, start in enumerate(starts):
            curve, cohort_trade_rows = run_c(start, factor, capture_trades=factor in (2, 5))
            curves.append(curve)
            if factor in (2, 5):
                trades.extend(annotate_cohort_trades(cohort_trade_rows, cohort_id))
        cohort_curves[str(START)] = cohort_curves.get(str(START), {})
        cohort_curves[str(START)][factor] = combine_cohort_curves(curves, starts)
        if factor in (2, 5):
            cohort_trades[factor] = trades
        all_runs[f"{START}_{factor}x"] = [{"cohort": index, "start": str(start)} for index, start in enumerate(starts)]

    phase_starts = {str(phase): cohort_start_dates(sessions, phase) for phase in PHASES}
    for phase in PHASES:
        if phase == START:
            continue
        curves = [run_c(start, 2, capture_trades=False)[0] for start in phase_starts[str(phase)]]
        cohort_curves[str(phase)] = {2: combine_cohort_curves(curves, phase_starts[str(phase)])}
        all_runs[f"{phase}_2x"] = [{"cohort": index, "start": str(start)} for index, start in enumerate(phase_starts[str(phase)])]

    stock_curves: dict[int, dict[str, pl.DataFrame]] = {factor: {} for factor in (2, 3, 4, 5)}
    stock_trades: dict[int, dict[str, list[dict]]] = {2: {}, 5: {}}
    for factor in (2, 3, 4, 5):
        for strategy_id in (A_STRATEGY, B_STRATEGY):
            path = STOCK_CACHE / f"{strategy_id}_{START}_{factor}x_p{POSITIONS[strategy_id]}.parquet"
            stock_curves[factor][strategy_id] = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
            if factor in (2, 5):
                trades_path = STOCK_CACHE / f"{strategy_id}_{START}_{factor}x_p{POSITIONS[strategy_id]}.trades.json"
                stock_trades[factor][strategy_id] = json.loads(trades_path.read_text(encoding="utf-8"))
        stock_curves[factor][C_STRATEGY] = cohort_curves[str(START)][factor]
    for phase in PHASES:
        for strategy_id in (A_STRATEGY, B_STRATEGY):
            path = STOCK_CACHE / f"{strategy_id}_{phase}_2x_p{POSITIONS[strategy_id]}.parquet"
            stock_curves.setdefault(2, {}).setdefault(f"{phase}:{strategy_id}", pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date"))
        stock_curves[2][f"{phase}:{C_STRATEGY}"] = cohort_curves[str(phase)][2]

    previous_target = r114.TARGET_VOL
    r114.TARGET_VOL = 0.15
    try:
        risk_curves, sleeve_weights, equity_curves = {}, {}, {}
        for factor in (2, 3, 4, 5):
            risk_curves[factor], sleeve_weights[factor] = r344._combine_three(
                (stock_curves[factor][A_STRATEGY], stock_curves[factor][B_STRATEGY], stock_curves[factor][C_STRATEGY]),
                r112.ROUND_TRIP_COST,
                C_WEIGHT_CAP,
            )
            equity_curves[factor] = r114._vol_target(risk_curves[factor])["curve"]
        risk_3c, _ = r344._combine_three(
            (stock_curves[3][A_STRATEGY], stock_curves[3][B_STRATEGY], stock_curves[3][C_STRATEGY]),
            r112.ROUND_TRIP_COST,
            C_WEIGHT_CAP,
        )
        equity_3c = r114._vol_target(risk_3c)["curve"]

        gold = r331.asset_frame("gold", "518880.SH")
        treasury = r331.asset_frame("treasury", "511260.SH")
        dividend = r331.asset_frame("dividend", "510880.SH")

        def build_bucket(equity_curve: pl.DataFrame, cost: float) -> dict:
            previous_lookback = r331.LOOKBACK
            previous_bucket_target = r331.TARGET_VOL
            previous_cap = r331.EQUITY_WEIGHT_MAX
            try:
                r331.LOOKBACK = 60
                r331.TARGET_VOL = 0.15
                r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
                alt = r344.gold_trend_proxy(gold, treasury, dividend, cost)
                return r331.build_three_asset_bucket(equity_curve, alt, treasury, dividend, round_trip_cost=cost)
            finally:
                r331.LOOKBACK = previous_lookback
                r331.TARGET_VOL = previous_bucket_target
                r331.EQUITY_WEIGHT_MAX = previous_cap

        bucket_curves = {
            2: build_bucket(equity_curves[2], OVERLAY_COST[2]),
            3: build_bucket(equity_curves[3], OVERLAY_COST[3]),
            "3c": build_bucket(equity_3c, OVERLAY_COST["3c"]),
            4: build_bucket(equity_curves[4], OVERLAY_COST[4]),
            5: build_bucket(equity_curves[5], OVERLAY_COST[5]),
        }
        phase_buckets = {}
        fresh = {}
        for phase in PHASES:
            curves = tuple(stock_curves[2][f"{phase}:{sid}"] for sid in STRATEGIES)
            risk, _ = r344._combine_three(curves, r112.ROUND_TRIP_COST, C_WEIGHT_CAP)
            equity = r114._vol_target(risk)["curve"]
            phase_bucket = build_bucket(equity, OVERLAY_COST[2])
            phase_buckets[phase] = phase_bucket
            fresh[str(phase)] = {
                "total_return": r112._stats(phase_bucket["curve"])["total_return"],
                "stats": r112._stats(phase_bucket["curve"]),
            }
    finally:
        r114.TARGET_VOL = previous_target

    industry_frame = r313.load_industry_l1(DATA)
    industry = {str(row["symbol"]): str(row["industry_l1"]) for row in industry_frame.iter_rows(named=True) if row["industry_l1"]}
    summaries = {}
    concentrations = {}
    for factor in (2, 5):
        trades = []
        for strategy_id in (A_STRATEGY, B_STRATEGY):
            trades.extend(stock_trades[factor][strategy_id])
        trades.extend(cohort_trades[factor])
        core = bucket_curves[factor]
        curve_dates = core["curve"]["date"].to_list()
        scaled = []
        for trade in trades:
            entry = r326.parse_day(trade["entry_date"])
            raw_exit = trade.get("exit_date")
            exit_day = r326.parse_day(raw_exit) if raw_exit and str(raw_exit) not in {"None", "null"} else END
            days = [day for day in curve_dates if entry <= day <= exit_day] or [entry]
            scales = []
            for day in days:
                equity_weight = r326.weight_at(core["rebalances"], day, "target_equity_weight")
                sleeve_weight = r326.weight_at(sleeve_weights[factor], day, f"weight_{trade['sleeve'].lower()}")
                scales.append(equity_weight * sleeve_weight)
            scale = sum(scales) / len(scales)
            scaled.append({**trade, "weight_scale": round(scale, 6), "pnl_amount": float(trade["pnl_amount"]) * scale})
        concentrations[f"{factor}x"] = r239._aggregate(scaled, industry)
        summaries[f"{factor}x"] = r112._stats(core["curve"])

    performance = {
        str(factor): {
            "stats": r112._stats(bucket_curves[factor]["curve"]),
            "yearly": r315.year_returns(bucket_curves[factor]["curve"]),
            "recent": segment_return(bucket_curves[factor]["curve"], RECENT_START),
            "segment_2022": segment_return(bucket_curves[factor]["curve"], START, date(2022, 12, 31)),
        }
        for factor in (2, 3, 4, 5)
    }
    performance["3x_conservative"] = {
        "stats": r112._stats(bucket_curves["3c"]["curve"]),
        "yearly": r315.year_returns(bucket_curves["3c"]["curve"]),
    }
    conc5 = concentrations["5x"]
    ctrl = json.loads((EVIDENCE / "r344_extended_window.json").read_text(encoding="utf-8"))
    candidate_2 = performance["2"]["stats"]
    control_2 = ctrl["center_2x"]["stats"]
    candidate_5 = performance["5"]["stats"]
    c5 = conc5
    fresh_positive = sum(1 for value in fresh.values() if value["total_return"] > 0)
    gates = {
        "G1_2x_annual_at_least_12pct": candidate_2["annual"] >= 0.12,
        "G2_2x_sharpe_at_least_1_25": candidate_2["sharpe"] >= 1.25,
        "G3_2x_mdd_at_least_minus_15pct": candidate_2["mdd"] >= -0.15,
        "G4_2022_segment_nonnegative": performance["2"]["segment_2022"] is not None and performance["2"]["segment_2022"] >= 0.0,
        "G5_2023_nonnegative": performance["2"]["yearly"].get("2023", -1.0) >= 0.0,
        "G6_recent_year_positive": (performance["2"]["recent"] or -1.0) > 0.0,
        "G7_2x_annual_not_below_control_by_more_than_1pp": candidate_2["annual"] >= control_2["annual"] - 0.01,
        "G8_2x_sharpe_not_below_control_by_more_than_0_10": candidate_2["sharpe"] >= control_2["sharpe"] - 0.10,
        "G9_5x_annual_at_least_10pct": candidate_5["annual"] >= 0.10,
        "G10_5x_sharpe_at_least_1_10": candidate_5["sharpe"] >= 1.10,
        "G11_5x_mdd_at_least_minus_15pct": candidate_5["mdd"] >= -0.15,
        "G12_5x_total_pnl_positive_for_concentration": c5["total_pnl_amount"] > 0,
        "G13_5x_top1_net_share_at_most_25pct": c5["total_pnl_amount"] > 0 and c5["top1_net_share"] <= 0.25,
        "G14_5x_top3_net_share_at_most_55pct": c5["total_pnl_amount"] > 0 and c5["top3_net_share"] <= 0.55,
        "G15_5x_top5_net_share_at_most_75pct": c5["total_pnl_amount"] > 0 and c5["top5_net_share"] <= 0.75,
        "G16_5x_symbol_breadth_not_below_control": c5["distinct_symbols"] >= ctrl["concentration_5x"]["concentration"]["distinct_symbols"],
        "G17_5x_industry_gross_share_at_most_45pct": c5["top_industry_gross_share"] is not None and c5["top_industry_gross_share"] <= 0.45,
        "G18_5x_max_symbol_loss_at_most_20pct_gross": c5["max_symbol_loss_to_gross"] is not None and c5["max_symbol_loss_to_gross"] <= 0.20,
        "G19_at_least_5_of_6_fresh_starts_positive": fresh_positive >= 5,
        "G20_no_leverage": all(bucket_curves[factor]["weight_summary"]["max_weight_sum"] <= 1.0 and bucket_curves[factor]["weight_summary"]["min_target_weight"] >= 0.0 for factor in (2, 3, 4, 5)),
    }
    after_r40 = r331.tree_hash(R40 / "kline_daily_enriched")
    after_prod = r331.tree_hash(DATA / "kline_daily_enriched")
    after_inputs = preflight_module.fingerprints()
    data_gates = {
        "D1_all_cohort_curves_start_at_frozen_sessions_and_end_on_END": all(
            cohort_curves[str(phase)][2]["date"].min() == phase and cohort_curves[str(phase)][2]["date"].max() == END
            for phase in PHASES
        ) and all(cohort_curves[str(START)][factor]["date"].min() == START and cohort_curves[str(START)][factor]["date"].max() == END for factor in (2, 3, 4, 5)),
        "D2_input_trees_unchanged": before_r40 == after_r40 and before_prod == after_prod,
        "D3_no_leverage": gates["G20_no_leverage"],
        "D4_all_frozen_inputs_unchanged": before_inputs == after_inputs,
    }
    all_pass = all(data_gates.values()) and all(gates.values())
    payload = {
        "round": "R359",
        "classification": "post-hoc execution-scale-corrected v2 replay of fixed three-cohort C-sleeve structure",
        "preregistration_sha256": sha256(PREREG),
        "preflight_sha256": sha256(PREFLIGHT),
        "preflight_amendment_sha256": sha256(PREFLIGHT_AMENDMENT),
        "execution_scale_erratum_sha256": sha256(ERRATUM),
        "frozen_input_fingerprints_sha256": hashlib.sha256(
            json.dumps(before_inputs, sort_keys=True).encode()
        ).hexdigest(),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "window": {"start": str(START), "end": str(END), "recent_start": str(RECENT_START)},
        "cohort_rule": {"offsets_trading_sessions": list(COHORT_OFFSETS), "phase_starts": phase_starts, "initial_capital_weights": [1.0 / 3.0] * 3, "initial_capital_per_account": 1_000_000.0 / 3.0, "pre_activation": "cash at zero return", "rebalancing_between_cohorts": False, "execution_scale_erratum": ERRATUM.name},
        "cohort_runs": all_runs,
        "cohort_cache_manifest": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(RUN_CACHE.iterdir())
            if path.is_file()
        },
        "candidate": {"performance": performance, "concentration_2x": concentrations["2x"], "concentration_5x": concentrations["5x"], "fresh_starts": fresh, "fresh_positive": fresh_positive},
        "v1_superseded_result": {"file": "r359_v1_cohort_backtest_execution_scale_error.json", "sha256": sha256(EVIDENCE / "r359_v1_cohort_backtest_execution_scale_error.json"), "reason": "V1 simulated each account at 1,000,000 CNY then divided trade P&L by three; this did not model the actual one-third account size. V2 reruns each C account at 333,333.33 CNY."},
        "control": {"r344_center_2x": ctrl["center_2x"], "r344_center_5x_concentration": ctrl["concentration_5x"]},
        "gates": gates,
        "data_gates": data_gates,
        "all_pass": all_pass,
        "selected": "r359_c_three_cohort_staggered" if all_pass else None,
        "decision": {"status": "candidate_for_full_robustness" if all_pass else "research_only_no_candidate", "paper_target_generated": False, "order_file_generated": False, "production_write": False, "no_leverage": True},
        "input_isolation": {"r40_before": before_r40, "r40_after": after_r40, "production_before": before_prod, "production_after": after_prod, "stable": before_r40 == after_r40 and before_prod == after_prod},
        "limits": [
            "R359 uses the research-set historical membership snapshots and inherits their documented survivor/membership caveats.",
            "Each C cohort starts with actual 333,333.33 CNY capital; trade P&L includes that account's lots and minimum commissions and is not post-scaled. The outer risk budget remains frozen to the R344 method.",
            "2x/3x/4x/5x labels mean transaction-cost multipliers, not capital leverage. The strategy is long-only, fully funded and uses no borrowing.",
            "The screen does not authorize live or paper trading; a pass requires independent robustness review.",
        ],
        "no_leverage": True,
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    for path, data in ((OUTPUT, raw),):
        with path.open("xb") as stream:
            stream.write(data)
        path.with_name(path.name + ".sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {path.name}\n", encoding="ascii")
    status = {
        "round": "R359",
        "status": "research_only_no_candidate_after_r359_cohort_ensemble" if not all_pass else "candidate_requires_independent_robustness",
        "selected": payload["selected"],
        "all_pass": all_pass,
        "failed_gates": [key for key, value in {**data_gates, **gates}.items() if not value],
        "paper_target_generated": False,
        "order_file_generated": False,
        "production_enabled": False,
        "no_leverage": True,
        "backtest": OUTPUT.name,
        "backtest_sha256": sha256(OUTPUT),
        "preflight": PREFLIGHT.name,
        "preregistration": PREREG.name,
    }
    status_raw = (json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    with STATUS.open("xb") as stream:
        stream.write(status_raw)
    STATUS.with_name(STATUS.name + ".sha256").write_text(f"{hashlib.sha256(status_raw).hexdigest()}  {STATUS.name}\n", encoding="ascii")
    result_text = f"""# R359 · C 袖三 cohort 错峰集成

## 规则

固定 3 个相同 C 子袖，分别在每个测试相位的第 0、20、40 个交易日启动。每个子袖独立使用原 C 策略、50 仓、60 日持有、T+1 开盘成交和对应成本；未启动资金持有现金；各子袖初始资本固定各占 1/3，不做 cohort 间再平衡。A/B、风险平价、C 10% 上限、15% 波动目标、黄金防守桶均沿用 R344。

## 结果

- 2×：年化 {performance['2']['stats']['annual']:.2%}，Sharpe {performance['2']['stats']['sharpe']:.4f}，MDD {performance['2']['stats']['mdd']:.2%}；2022 {performance['2']['segment_2022']:.2%}，近一年 {performance['2']['recent']:.2%}。
- 5×：年化 {performance['5']['stats']['annual']:.2%}，Sharpe {performance['5']['stats']['sharpe']:.4f}，MDD {performance['5']['stats']['mdd']:.2%}；top1/top3/top5 {c5['top1_net_share']:.2%}/{c5['top3_net_share']:.2%}/{c5['top5_net_share']:.2%}，股票数 {c5['distinct_symbols']}。
- 6 个 fresh starts 正收益 {fresh_positive}/6；通过门槛 {sum(gates.values())}/{len(gates)}，最终 `selected={payload['selected']}`。
- 无杠杆，无纸面目标，无订单，生产未启用。

## 证据

- Backtest: `{OUTPUT.name}` SHA-256 `{sha256(OUTPUT)}`
- Preregistration: `{PREREG.name}` SHA-256 `{sha256(PREREG)}`
- Preflight: `{PREFLIGHT.name}` SHA-256 `{sha256(PREFLIGHT)}`

"""
    with RESULT_MD.open("x", encoding="utf-8") as stream:
        stream.write(result_text)
    RESULT_MD.with_name(RESULT_MD.name + ".sha256").write_text(f"{sha256(RESULT_MD)}  {RESULT_MD.name}\n", encoding="ascii")
    print(json.dumps({"all_pass": all_pass, "selected": payload["selected"], "candidate_2x": performance["2"], "candidate_5x": performance["5"], "concentration_5x": {key: c5[key] for key in ("top1_net_share", "top3_net_share", "top5_net_share", "distinct_symbols")}, "fresh_positive": fresh_positive, "failed_gates": status["failed_gates"], "output": str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2, default=str))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
