"""R380 A1: rebuild the candidate on the current engine, then audit trades."""
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
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
R40 = RESEARCH / "r40_engine_data"
PREREG = EVIDENCE / "r380_preregistration_amendment_A1.json"
FAILURE = EVIDENCE / "r380_attempt1_failure.json"
R374 = EVIDENCE / "r374_platform_validation.json"
R377 = EVIDENCE / "r377_holdout.json"
R378 = EVIDENCE / "r378_cost_stress.json"
OUTPUT = EVIDENCE / "r380_trade_audit_v2.json"
RESULT = EVIDENCE / "r380_result_v2.md"
CACHE = RESEARCH / "r380_v2_cache"
CELL = "fade05_h30_sl20"
CONTROL_CELL = "fade05_h20_sl20"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "r371_industry_fade_5d_quality_h20_sl20"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
SEALED_START = date(2025, 5, 6)
RECENT_START = date(2025, 11, 5)
ROLLING_STARTS = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5), date(2025, 5, 5))
COHORT_STARTS = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
INITIAL_CAPITAL = 1_000_000.0
SCENARIOS = {
    "2x": {
        "stock": {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0},
        "portfolio_turnover": 0.0052,
        "defensive_turnover": 0.001,
    },
    "3x": {
        "stock": {"fees": 0.0006, "commission": 0.0006, "stamp": 0.001, "slippage": 15.0},
        "portfolio_turnover": 0.0078,
        "defensive_turnover": 0.0015,
    },
}
CAPITAL_LEVELS = (10_000_000, 50_000_000, 100_000_000, 250_000_000, 500_000_000, 1_000_000_000)
ATTRITION = 0.10
sys.path.insert(0, str(ROOT / "backend"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def parse_day(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def curve_frame(values: list[dict[str, Any]]) -> pl.DataFrame:
    rows = [{"date": parse_day(item["date"]), "value": float(item["value"])} for item in values]
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def read_curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def stats(values: list[float]) -> dict[str, float | int]:
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    volatility = st.pstdev(returns)
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": total,
        "annual": (1.0 + total) ** (252.0 / len(returns)) - 1.0,
        "sharpe": st.mean(returns) / volatility * math.sqrt(252.0) if volatility else 0.0,
        "mdd": mdd,
        "days": len(values),
    }


def segment(frame: pl.DataFrame, start: date, end: date = END) -> dict[str, Any]:
    part = frame.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        raise ValueError(f"segment {start}..{end} is too short")
    return {**stats([float(value) for value in part["value"].to_list()]), "start": str(part["date"][0]), "end": str(part["date"][-1])}


def year_returns(frame: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    result: dict[str, float] = {}
    previous: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        base = previous if previous is not None else values[0]
        result[str(year)] = values[-1] / base - 1.0
        previous = values[-1]
    return result


def rolling_positive(frame: pl.DataFrame) -> dict[str, Any]:
    windows = []
    for start in ROLLING_STARTS:
        part = frame.filter(pl.col("date") >= start).sort("date").head(253)
        if part.height != 253:
            raise ValueError(f"rolling window from {start} is incomplete")
        values = part["value"].to_list()
        windows.append({"start": str(start), "return": float(values[-1] / values[0] - 1.0)})
    return {
        "window_count": len(windows),
        "positive_count": sum(window["return"] > 0 for window in windows),
        "windows": windows,
    }


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def weight_at(records: list[dict[str, Any]], day: date, key: str) -> float:
    value = float(records[0][key])
    for record in records:
        if parse_day(record["date"]) <= day:
            value = float(record[key])
        else:
            break
    return value


def cache_key(kind: str, strategy_id: str, start: date, cost: str, hold: int | None = None) -> str:
    suffix = f"_h{hold}" if hold is not None else ""
    return f"{kind}_{strategy_id}_{start}_{cost}{suffix}"


def load_cached(cache_key_name: str, meta: dict[str, Any]) -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None:
    path = CACHE / f"{cache_key_name}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("meta") != meta:
        raise RuntimeError(f"R380 current-engine cache metadata mismatch: {cache_key_name}")
    return (
        read_curve(CACHE / f"{cache_key_name}.parquet"),
        payload["trades"],
        payload["stats"],
        payload["run"],
    )


def save_cached(cache_key_name: str, meta: dict[str, Any], curve: pl.DataFrame, trades: list[dict[str, Any]], result_stats: dict[str, Any], elapsed: float):
    CACHE.mkdir(parents=True, exist_ok=True)
    curve.write_parquet(CACHE / f"{cache_key_name}.parquet", compression="zstd")
    payload = {
        "meta": meta,
        "trades": trades,
        "stats": result_stats,
        "run": {"elapsed_seconds": elapsed, "cache_hit": False},
    }
    (CACHE / f"{cache_key_name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def run_current_chain() -> dict[str, Any]:
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r371 = module(EVIDENCE / "run_r371_alpha_parameter_grid.py", "r380v2_r371")
    r371._write_variant_definitions()
    membership = r371._membership()
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    variant_paths = {
        "B": r371.VARIANTS / "strategies" / f"{B_STRATEGY}.py",
        "C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
        "A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
    }
    engine_hash = sha256(ROOT / "backend/app/backtest/engine.py")
    work = Path(tempfile.mkdtemp(prefix="r380v2_engine_data_"))
    old_data = settings.data_dir
    outputs: dict[str, Any] = {}
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r380v2_isolation").prepare_isolated_data_root(engine_root, DATA)
        for name in ("custom_factors", "custom_signals"):
            for source in sorted((r371.VARIANTS / name).glob("*")):
                shutil.copy2(source, engine_root / "user_data" / name / source.name)
        settings.data_dir = engine_root
        load_into_registry(engine_root)
        pipeline.invalidate_custom_signals()
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai", r371.VARIANTS / "strategies"],
            override_loader=lambda _sid: {},
            industry_loader=industry_loader_for(engine_root),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run_one(
            kind: str,
            strategy_id: str,
            start: date,
            cost: str,
            max_positions: int,
            *,
            overrides: dict[str, Any] | None = None,
            hold: int | None = None,
            initial_capital: float = INITIAL_CAPITAL,
        ):
            scenario = SCENARIOS[cost]
            stock = scenario["stock"]
            meta = {
                "round": "R380-A1",
                "kind": kind,
                "strategy_id": strategy_id,
                "start": str(start),
                "end": str(END),
                "cost": cost,
                "fees_pct": stock["fees"],
                "commission_pct": stock["commission"],
                "stamp_tax_pct": stock["stamp"],
                "slippage_bps": stock["slippage"],
                "matching": "open_t+1",
                "max_positions": max_positions,
                "overrides": overrides or {},
                "hold": hold,
                "initial_capital": initial_capital,
                "membership_sha256": membership_hash,
                "strategy_sha256": sha256(variant_paths[kind]),
                "engine_sha256": engine_hash,
            }
            key = cache_key(kind, strategy_id, start, cost, hold)
            cached = load_cached(key, meta)
            if cached is not None:
                return cached
            config = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides=overrides or {},
                matching="open_t+1",
                fees_pct=stock["fees"],
                commission_pct=stock["commission"],
                stamp_tax_pct=stock["stamp"],
                slippage_bps=stock["slippage"],
                holding_days=hold,
                initial_capital=initial_capital,
                max_positions=max_positions,
                max_exposure_pct=1.0,
                position_sizing="equal",
                mode="position",
                asset_type="stock",
                hs300=True,
            )
            started = time.perf_counter()
            with r371._patched_membership(membership):
                result = service.run(config)
            if result.error:
                raise RuntimeError(f"R380-A1 {kind} {strategy_id} {cost}: {result.error}")
            curve = curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != END:
                raise RuntimeError(f"R380-A1 {kind} {strategy_id} {cost}: date coverage mismatch")
            elapsed = time.perf_counter() - started
            result_stats = {
                "execution": result.stats.get("execution") or {},
                "selection": result.stats.get("selection") or {},
                "stats": {
                    key_name: result.stats.get(key_name)
                    for key_name in ("total_return", "annual_return", "sharpe", "max_drawdown", "n_trades")
                    if key_name in result.stats
                },
            }
            save_cached(key, meta, curve, result.trades, result_stats, elapsed)
            print(f"R380-A1 ran {kind} {strategy_id} {cost}: trades={len(result.trades)} elapsed={elapsed:.1f}s", flush=True)
            return curve, result.trades, result_stats, {"elapsed_seconds": elapsed, "cache_hit": False}

        for cost in ("2x", "3x"):
            outputs[f"A_{cost}"] = run_one("A", A_STRATEGY, START, cost, 10)
            outputs[f"B30_{cost}"] = run_one(
                "B", B_STRATEGY, START, cost, 10,
                overrides={"max_hold_days": 30, "stop_loss": -0.20}, hold=30,
            )
            if cost == "2x":
                outputs[f"B20_{cost}"] = run_one(
                    "B", B_STRATEGY, START, cost, 10,
                    overrides={"max_hold_days": 20, "stop_loss": -0.20}, hold=20,
                )
            for index, cohort_start in enumerate(COHORT_STARTS):
                outputs[f"C_{cost}_{index}"] = run_one(
                    "C", C_STRATEGY, cohort_start, cost, 50,
                    initial_capital=INITIAL_CAPITAL / 3.0,
                )
        return {
            "outputs": outputs,
            "membership_sha256": membership_hash,
            "engine_sha256": engine_hash,
            "variant_sha256": {key: sha256(path) for key, path in variant_paths.items()},
            "engine_root": str(engine_root),
        }
    finally:
        settings.data_dir = old_data
        shutil.rmtree(work, ignore_errors=True)


def combine_c(outputs: dict[str, Any], cost: str) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r380v2_combine")
    curves = [outputs[f"C_{cost}_{index}"][0] for index in range(3)]
    curve = r359.combine_cohort_curves(curves, list(COHORT_STARTS))
    trades: list[dict[str, Any]] = []
    for index in range(3):
        trades.extend(
            {"sleeve": "C", **trade}
            for trade in r359.annotate_cohort_trades(outputs[f"C_{cost}_{index}"][1], index)
        )
    return curve, trades


def portfolio_bundle(ctx: dict[str, Any], a: pl.DataFrame, b: pl.DataFrame, c: pl.DataFrame, cost: str) -> dict[str, Any]:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    scenario = SCENARIOS[cost]
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, scenario["defensive_turnover"])
    old_112 = r112.ROUND_TRIP_COST
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, sleeve_weights = r344._combine_three((a, b, c), scenario["portfolio_turnover"], 0.10)
        equity = r114._vol_target(risk)["curve"]
        final = r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            treasury,
            dividend,
            round_trip_cost=scenario["defensive_turnover"],
        )
        return {
            "curve": final["curve"],
            "risk_dates": risk["date"].to_list(),
            "sleeve_weights": sleeve_weights,
            "rebalances": final["rebalances"],
        }
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def trade_scale(bundle: dict[str, Any], trade: dict[str, Any], day: date) -> float:
    sleeve = str(trade["sleeve"]).lower()
    return (
        weight_at(bundle["rebalances"], day, "target_equity_weight")
        * weight_at(bundle["sleeve_weights"], day, f"weight_{sleeve}")
    )


def scale_trades(trades: list[dict[str, Any]], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    risk_dates = bundle["risk_dates"]
    scaled = []
    for trade in trades:
        entry = parse_day(trade["entry_date"])
        exit_day = parse_day(trade["exit_date"])
        days = [day for day in risk_dates if entry <= day <= exit_day] or [entry]
        average_scale = st.mean(trade_scale(bundle, trade, day) for day in days)
        pnl_amount = float(trade.get("pnl_amount") or 0.0)
        entry_value = float(trade.get("entry_value") or 0.0)
        exit_value = float(trade.get("exit_value") or 0.0)
        scaled.append({
            **trade,
            "return_weight": average_scale,
            "portfolio_pnl_amount": pnl_amount * average_scale,
            "portfolio_entry_notional": entry_value * trade_scale(bundle, trade, entry),
            "portfolio_exit_notional": exit_value * trade_scale(bundle, trade, exit_day),
            "blocked_exit_days": int(trade.get("blocked_exit_days") or 0),
        })
    return scaled


def all_current_trades(outputs: dict[str, Any], c_trades_2x: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a = [{**trade, "sleeve": "A"} for trade in outputs["A_2x"][1]]
    b = [{**trade, "sleeve": "B"} for trade in outputs["B30_2x"][1]]
    return [*a, *b, *c_trades_2x]


def amount_lookup() -> dict[tuple[str, date], float]:
    r40_latest = max(path.parent.name for path in (R40 / "kline_daily_enriched").glob("date=*/part.parquet"))
    r40_latest_date = date.fromisoformat(r40_latest.split("=")[1])
    paths = sorted((R40 / "kline_daily_enriched").glob("date=*/part.parquet"))
    paths.extend(
        path for path in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
        if date.fromisoformat(path.parent.name.split("=")[1]) > r40_latest_date
    )
    frame = (
        pl.scan_parquet(paths).select(["symbol", "date", "amount"]).collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("amount").cast(pl.Float64))
    )
    return {
        (str(row["symbol"]), row["date"]): float(row["amount"])
        for row in frame.iter_rows(named=True)
        if row["amount"] is not None
    }


def capacity_audit(trades: list[dict[str, Any]], amounts: dict[tuple[str, date], float]) -> dict[str, Any]:
    missing = []
    rows = []
    for trade in trades:
        entry = parse_day(trade["entry_date"])
        exit_day = parse_day(trade["exit_date"])
        entry_amount = amounts.get((str(trade["symbol"]), entry))
        exit_amount = amounts.get((str(trade["symbol"]), exit_day))
        if not entry_amount or not exit_amount:
            missing.append({
                "symbol": trade["symbol"], "entry_date": str(entry), "exit_date": str(exit_day),
                "entry_amount": entry_amount, "exit_amount": exit_amount,
            })
        rows.append({**trade, "entry_amount": entry_amount, "exit_amount": exit_amount})
    levels = []
    for capital in CAPITAL_LEVELS:
        multiplier = capital / INITIAL_CAPITAL
        participation = []
        for row in rows:
            if not row["entry_amount"] or not row["exit_amount"]:
                continue
            entry_share = float(row["portfolio_entry_notional"]) * multiplier / row["entry_amount"]
            exit_share = float(row["portfolio_exit_notional"]) * multiplier / row["exit_amount"]
            participation.append(max(entry_share, exit_share))
        p95 = quantile(participation, 0.95)
        p99 = quantile(participation, 0.99)
        p95_days = quantile([value / ATTRITION for value in participation], 0.95)
        passes = bool(
            p95 is not None and p99 is not None and p95_days is not None
            and p95 <= 0.05 and p99 <= 0.10 and p95_days <= 1.0
        )
        levels.append({
            "capital_cny": capital,
            "p95_participation": p95,
            "p99_participation": p99,
            "p95_days_to_liquidate_at_10pct": p95_days,
            "entry_exit_observations": len(participation),
            "above_5pct_count": sum(value > 0.05 for value in participation),
            "above_10pct_count": sum(value > 0.10 for value in participation),
            "passes": passes,
        })
    passing = [row["capital_cny"] for row in levels if row["passes"]]
    return {
        "capital_levels": levels,
        "missing_daily_amount": missing,
        "maximum_passing_level_cny": max(passing) if passing else None,
        "one_hundred_million_gate_passes": next(row["passes"] for row in levels if row["capital_cny"] == 100_000_000),
    }


def execution_audit(stats_payload: dict[str, Any], b_trades: list[dict[str, Any]]) -> dict[str, Any]:
    execution = stats_payload.get("execution") or {}
    hard_buy = ["buy_limit_up", "buy_suspended", "buy_invalid_price", "buy_cash", "buy_exposure"]
    hard_sell = ["sell_limit_down", "sell_suspended", "sell_invalid_price"]
    buy_misses = sum(int(execution.get(key, 0)) for key in hard_buy)
    sell_misses = sum(int(execution.get(key, 0)) for key in hard_sell)
    n_trades = len(b_trades)
    blocked = [trade for trade in b_trades if int(trade.get("blocked_exit_days") or 0) > 0]
    return {
        "execution_counters": execution,
        "selection_counters": stats_payload.get("selection") or {},
        "hard_entry_misses": buy_misses,
        "hard_exit_misses": sell_misses,
        "hard_entry_miss_rate": buy_misses / (buy_misses + n_trades) if n_trades else None,
        "hard_exit_miss_rate": sell_misses / (sell_misses + n_trades) if n_trades else None,
        "blocked_exit_trades": len(blocked),
        "blocked_exit_trade_share": len(blocked) / n_trades if n_trades else None,
        "max_blocked_exit_days": max((int(trade.get("blocked_exit_days") or 0) for trade in b_trades), default=0),
        "no_slot_count": int(execution.get("buy_no_slot", 0)),
        "same_day_reentry_count": int(execution.get("buy_same_day_reentry", 0)),
        "trade_count": n_trades,
    }


def industry_map() -> dict[str, str]:
    from app.services.industry_map import load_industry_l1

    frame = load_industry_l1(DATA)
    return {
        str(row["symbol"]): str(row["industry_l1"])
        for row in frame.iter_rows(named=True)
        if row["industry_l1"]
    }


def build_report() -> dict[str, Any]:
    for path in (PREREG, FAILURE):
        checksum = path.with_name(path.name + ".sha256")
        if checksum.read_text(encoding="ascii").split()[0] != sha256(path):
            raise RuntimeError(f"R380-A1 source checksum mismatch: {path.name}")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    old374 = json.loads(R374.read_text(encoding="utf-8"))
    old377 = json.loads(R377.read_text(encoding="utf-8"))
    old378 = json.loads(R378.read_text(encoding="utf-8"))
    old_center = next(row for row in old374["rows"] if row["cell"] == CELL)
    chain = run_current_chain()
    outputs = chain["outputs"]
    c2, c_trades_2x = combine_c(outputs, "2x")
    c3, _ = combine_c(outputs, "3x")
    ctx = {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r380v2_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r380v2_r344"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r380v2_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r380v2_r114"),
    }
    bundle_center_2x = portfolio_bundle(ctx, outputs["A_2x"][0], outputs["B30_2x"][0], c2, "2x")
    bundle_control_2x = portfolio_bundle(ctx, outputs["A_2x"][0], outputs["B20_2x"][0], c2, "2x")
    bundle_center_3x = portfolio_bundle(ctx, outputs["A_3x"][0], outputs["B30_3x"][0], c3, "3x")
    current = {
        "center_2x": {"full": stats(bundle_center_2x["curve"]["value"].to_list()), "yearly": year_returns(bundle_center_2x["curve"]), "recent": segment(bundle_center_2x["curve"], RECENT_START), "sealed": segment(bundle_center_2x["curve"], SEALED_START), "rolling": rolling_positive(bundle_center_2x["curve"])},
        "control_2x": {"full": stats(bundle_control_2x["curve"]["value"].to_list()), "yearly": year_returns(bundle_control_2x["curve"]), "recent": segment(bundle_control_2x["curve"], RECENT_START), "sealed": segment(bundle_control_2x["curve"], SEALED_START), "rolling": rolling_positive(bundle_control_2x["curve"])},
        "center_3x": {"full": stats(bundle_center_3x["curve"]["value"].to_list()), "yearly": year_returns(bundle_center_3x["curve"]), "recent": segment(bundle_center_3x["curve"], RECENT_START), "sealed": segment(bundle_center_3x["curve"], SEALED_START), "rolling": rolling_positive(bundle_center_3x["curve"])},
    }
    reconciliation_prereg = prereg["reconciliation_gates"]
    c2_gate = reconciliation_prereg["current_2x_full"]
    c3_gate = reconciliation_prereg["current_3x_full"]
    sealed_gate = reconciliation_prereg["current_sealed"]
    reconciliation_checks = {
        "2x_annual": current["center_2x"]["full"]["annual"] >= c2_gate["annual_min"],
        "2x_sharpe": current["center_2x"]["full"]["sharpe"] >= c2_gate["sharpe_min"],
        "2x_mdd": current["center_2x"]["full"]["mdd"] >= c2_gate["mdd_min"],
        "2x_2022": current["center_2x"]["yearly"].get("2022", -1.0) >= c2_gate["year_2022_min"],
        "2x_2023": current["center_2x"]["yearly"].get("2023", -1.0) >= c2_gate["year_2023_min"],
        "2x_recent": current["center_2x"]["recent"]["total_return"] > c2_gate["recent_return_min_exclusive"],
        "2x_rolling": current["center_2x"]["rolling"]["positive_count"] >= c2_gate["rolling_positive_count_min"],
        "3x_annual": current["center_3x"]["full"]["annual"] >= c3_gate["annual_min"],
        "3x_sharpe": current["center_3x"]["full"]["sharpe"] >= c3_gate["sharpe_min"],
        "3x_mdd": current["center_3x"]["full"]["mdd"] >= c3_gate["mdd_min"],
        "sealed_positive": current["center_2x"]["sealed"]["total_return"] > 0.0,
        "sealed_sharpe": current["center_2x"]["sealed"]["sharpe"] >= sealed_gate["centre_sharpe_min"],
        "sealed_mdd": current["center_2x"]["sealed"]["mdd"] >= sealed_gate["centre_mdd_min"],
        "sealed_beats_control_return": current["center_2x"]["sealed"]["total_return"] > current["control_2x"]["sealed"]["total_return"],
        "sealed_beats_control_sharpe": current["center_2x"]["sealed"]["sharpe"] >= current["control_2x"]["sealed"]["sharpe"],
    }
    reconciliation_pass = all(reconciliation_checks.values())
    all_trades = all_current_trades(outputs, c_trades_2x)
    scaled = scale_trades(all_trades, bundle_center_2x)
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r380v2_r239")
    industry = industry_map()
    full = r239._aggregate(scaled, industry)
    recent_trades = [trade for trade in scaled if parse_day(trade["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    b_full = r239._aggregate([trade for trade in scaled if trade["sleeve"] == "B"], industry)
    recent_positive = sum(row["pnl_amount"] > 0 for row in recent["per_symbol"])
    execution = execution_audit(outputs["B30_2x"][2], outputs["B30_2x"][1])
    capacity = capacity_audit(scaled, amount_lookup())
    audit_gates = prereg["trade_audit_gates"]
    concentration_checks = {
        "distinct_symbols": full["distinct_symbols"] >= audit_gates["concentration"]["distinct_symbols_min"],
        "top1_gross": full["top1_gross_share"] is not None and full["top1_gross_share"] <= audit_gates["concentration"]["top1_gross_profit_share_max"],
        "top3_gross": full["top3_gross_share"] is not None and full["top3_gross_share"] <= audit_gates["concentration"]["top3_gross_profit_share_max"],
        "top5_gross": full["top5_gross_share"] is not None and full["top5_gross_share"] <= audit_gates["concentration"]["top5_gross_profit_share_max"],
        "top_industry_gross": full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= audit_gates["concentration"]["top_industry_gross_profit_share_max"],
        "max_symbol_loss": full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= audit_gates["concentration"]["max_single_symbol_loss_to_gross_profit_max"],
        "recent_positive_symbols": recent_positive >= audit_gates["concentration"]["recent_positive_symbols_min"],
    }
    execution_checks = {
        "hard_entry_miss_rate": execution["hard_entry_miss_rate"] is not None and execution["hard_entry_miss_rate"] <= audit_gates["execution"]["hard_entry_miss_rate_max"],
        "hard_exit_miss_rate": execution["hard_exit_miss_rate"] is not None and execution["hard_exit_miss_rate"] <= audit_gates["execution"]["hard_exit_miss_rate_max"],
        "blocked_exit_trade_share": execution["blocked_exit_trade_share"] is not None and execution["blocked_exit_trade_share"] <= audit_gates["execution"]["blocked_exit_trade_share_max"],
        "max_blocked_exit_days": execution["max_blocked_exit_days"] <= audit_gates["execution"]["max_blocked_exit_days_per_trade_max"],
    }
    capacity_checks = {
        "one_hundred_million": capacity["one_hundred_million_gate_passes"],
        "no_missing_daily_amount": len(capacity["missing_daily_amount"]) == 0,
    }
    audit_checks = {
        "concentration": all(concentration_checks.values()),
        "execution": all(execution_checks.values()),
        "capacity": all(capacity_checks.values()),
    }
    audit_pass = all(audit_checks.values())
    old_vs_current = {
        "center_2x_annual_delta": current["center_2x"]["full"]["annual"] - old_center["annual_2x"],
        "center_2x_sharpe_delta": current["center_2x"]["full"]["sharpe"] - old_center["sharpe_2x"],
        "center_2x_mdd_delta": current["center_2x"]["full"]["mdd"] - old_center["mdd_2x"],
        "center_3x_annual_delta": current["center_3x"]["full"]["annual"] - old_center["annual_3x"],
        "center_3x_sharpe_delta": current["center_3x"]["full"]["sharpe"] - old_center["sharpe_3x"],
        "sealed_center_return_delta": current["center_2x"]["sealed"]["total_return"] - old377["sealed"][CELL]["total_return"],
        "sealed_control_return_delta": current["control_2x"]["sealed"]["total_return"] - old377["sealed"][CONTROL_CELL]["total_return"],
        "old_center_3x_annual_from_r378": old378["rows"]["3x"][CELL]["annual"],
    }
    pre_fix_label = not all(math.isclose(old_vs_current[key], 0.0, abs_tol=1e-9) for key in (
        "center_2x_annual_delta", "center_2x_sharpe_delta", "center_2x_mdd_delta"
    ))
    trade_counts = {sleeve: sum(trade["sleeve"] == sleeve for trade in scaled) for sleeve in ("A", "B", "C")}
    trade_counts["total"] = len(scaled)
    return {
        "round": "R380",
        "amendment": "A1",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "failure_sha256": sha256(FAILURE),
        "engine_identity": {key: chain[key] for key in ("membership_sha256", "engine_sha256", "variant_sha256")},
        "run_summary": {
            key: {"cache_hit": value[3]["cache_hit"], "elapsed_seconds": value[3]["elapsed_seconds"], "trades": len(value[1])}
            for key, value in outputs.items()
        },
        "old_vs_current": old_vs_current,
        "old_engine_vintage_label": "pre_fix_vintage_R374_R379" if pre_fix_label else "exact_reconstruction",
        "current_portfolio": current,
        "reconciliation_checks": reconciliation_checks,
        "reconciliation_pass": reconciliation_pass,
        "trade_counts": trade_counts,
        "concentration_full": {key: value for key, value in full.items() if key != "per_symbol"},
        "concentration_recent": {key: value for key, value in recent.items() if key != "per_symbol"},
        "concentration_b_sleeve": {key: value for key, value in b_full.items() if key != "per_symbol"},
        "recent_positive_symbols": recent_positive,
        "concentration_checks": concentration_checks,
        "execution": execution,
        "execution_checks": execution_checks,
        "capacity": capacity,
        "capacity_checks": capacity_checks,
        "sleeve_pnl": {
            sleeve: sum(trade["portfolio_pnl_amount"] for trade in scaled if trade["sleeve"] == sleeve)
            for sleeve in ("A", "B", "C")
        },
        "audit_checks": audit_checks,
        "all_pass": reconciliation_pass and audit_pass,
        "trades": scaled,
        "decision": {
            "engine_reconciliation_pass": reconciliation_pass,
            "trade_level_audit_pass": audit_pass,
            "status": (
                "candidate_for_paper_trading_review"
                if reconciliation_pass and audit_pass
                else "research_only_current_engine_revalidation_failed"
                if not reconciliation_pass
                else "research_only_trade_audit_failed"
            ),
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    current = report["current_portfolio"]
    full = report["concentration_full"]
    execution = report["execution"]
    capacity = report["capacity"]
    cap100 = next(row for row in capacity["capital_levels"] if row["capital_cny"] == 100_000_000)
    lines = [
        "# R380-A1 · 当前引擎重建与 hold-30 交易级审计",
        "",
        f"旧 R371 2× 曲线与当前引擎不一致，已标记为 `{report['old_engine_vintage_label']}`；本轮用当前引擎重建 A/B/B20/C 全链。",
        "",
        "| 当前引擎组合 | 年化 | Sharpe | MDD | 近一年 | 封存段 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| hold-30 中心 2× | {current['center_2x']['full']['annual']:.2%} | {current['center_2x']['full']['sharpe']:.3f} | {current['center_2x']['full']['mdd']:.2%} | {current['center_2x']['recent']['total_return']:.2%} | {current['center_2x']['sealed']['total_return']:.2%} |",
        f"| 20 日对照 2× | {current['control_2x']['full']['annual']:.2%} | {current['control_2x']['full']['sharpe']:.3f} | {current['control_2x']['full']['mdd']:.2%} | {current['control_2x']['recent']['total_return']:.2%} | {current['control_2x']['sealed']['total_return']:.2%} |",
        f"| hold-30 中心 3× | {current['center_3x']['full']['annual']:.2%} | {current['center_3x']['full']['sharpe']:.3f} | {current['center_3x']['full']['mdd']:.2%} | {current['center_3x']['recent']['total_return']:.2%} | {current['center_3x']['sealed']['total_return']:.2%} |",
        "",
        f"- 旧/当前 2× 年化差 {report['old_vs_current']['center_2x_annual_delta']:+.2%}，Sharpe 差 {report['old_vs_current']['center_2x_sharpe_delta']:+.3f}，MDD 差 {report['old_vs_current']['center_2x_mdd_delta']:+.2%}。",
        f"- 当前引擎复核：{report['reconciliation_pass']}，{sum(report['reconciliation_checks'].values())}/{len(report['reconciliation_checks'])} 项通过。",
        "",
        "| 集中度 | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 股票数 | {full['distinct_symbols']} | >=80 |",
        f"| top1 毛利润占比 | {full['top1_gross_share']:.2%} | <=25% |",
        f"| top3 毛利润占比 | {full['top3_gross_share']:.2%} | <=55% |",
        f"| top5 毛利润占比 | {full['top5_gross_share']:.2%} | <=75% |",
        f"| 最大行业毛利润占比 | {full['top_industry_gross_share']:.2%} | <=45% |",
        f"| 最大单票亏损/毛利润 | {full['max_symbol_loss_to_gross']:.2%} | <=20% |",
        f"| 近一年盈利股票数 | {report['recent_positive_symbols']} | >=8 |",
        "",
        "| 执行 | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| 硬性买入未成交率 | {execution['hard_entry_miss_rate']:.2%} | <=5% |",
        f"| 硬性卖出未成交率 | {execution['hard_exit_miss_rate']:.2%} | <=5% |",
        f"| 有阻塞卖出的交易占比 | {execution['blocked_exit_trade_share']:.2%} | <=5% |",
        f"| 单笔最长阻塞卖出天数 | {execution['max_blocked_exit_days']} | <=10 |",
        "",
        "| 容量（1亿元） | 结果 | 门槛 |",
        "| --- | ---: | ---: |",
        f"| P95 单笔占当日成交额 | {cap100['p95_participation']:.2%} | <=5% |",
        f"| P99 单笔占当日成交额 | {cap100['p99_participation']:.2%} | <=10% |",
        f"| P95 按10%参与率清仓天数 | {cap100['p95_days_to_liquidate_at_10pct']:.2f} | <=1.0 |",
        "",
        f"- 综合判定：{'通过' if report['all_pass'] else '未通过'}，状态 `{report['decision']['status']}`。",
        "- 旧 R374-R379 结论只在旧引擎 vintage 内有效；当前引擎结果以本文件为准。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R380-A1 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps({
        "old_engine_vintage_label": report["old_engine_vintage_label"],
        "old_vs_current": report["old_vs_current"],
        "reconciliation_checks": report["reconciliation_checks"],
        "reconciliation_pass": report["reconciliation_pass"],
        "audit_checks": report["audit_checks"],
        "all_pass": report["all_pass"],
        "decision": report["decision"],
        "concentration": report["concentration_full"],
        "execution": {key: report["execution"][key] for key in (
            "hard_entry_miss_rate", "hard_exit_miss_rate", "blocked_exit_trade_share", "max_blocked_exit_days"
        )},
        "capacity": report["capacity"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
