"""R380: trade-level concentration, execution and capacity audit of the hold-30 centre."""
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
PREREG = EVIDENCE / "r380_preregistration.json"
R374 = EVIDENCE / "r374_platform_validation.json"
R378 = EVIDENCE / "r378_cost_stress.json"
OUTPUT = EVIDENCE / "r380_trade_audit.json"
RESULT = EVIDENCE / "r380_result.md"
CACHE = RESEARCH / "r380_cache"
A_TRADES = RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.trades.json"
C_TRADES = (
    RESEARCH / "r359_v2_cache/ai_hs300_quality_momentum_broad_h60_2022-05-05_2x_p50.trades.json",
    RESEARCH / "r359_v2_cache/ai_hs300_quality_momentum_broad_h60_2022-06-02_2x_p50.trades.json",
    RESEARCH / "r359_v2_cache/ai_hs300_quality_momentum_broad_h60_2022-07-01_2x_p50.trades.json",
)
R371_CACHE = RESEARCH / "r371_cache"
CELL = "fade05_h30_sl20"
FADE = 5
HOLD = 30
STOP = -0.20
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
INITIAL_CAPITAL = 1_000_000.0
ROUND_TRIP_COST = 0.0052
DEFENSIVE_COST = 0.001
ATTRITION = 0.10
COSTS_2X = {"fees": 0.0004, "commission": 0.0004, "stamp": 0.0005, "slippage": 10.0}
CAPITAL_LEVELS = (10_000_000, 50_000_000, 100_000_000, 250_000_000, 500_000_000, 1_000_000_000)
POSITIONS = {"A": 10, "B": 10, "C": 50}
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


def _curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for item in curve:
        rows.append({"date": parse_day(item["date"]), "value": float(item["value"])})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


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


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def weight_at(records: list[dict[str, Any]], day: date, key: str) -> float:
    if not records:
        raise ValueError("weight records cannot be empty")
    value = float(records[0][key])
    for record in records:
        record_day = parse_day(record["date"])
        if record_day <= day:
            value = float(record[key])
        else:
            break
    return value


def run_b_trades() -> tuple[pl.DataFrame, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r371 = module(EVIDENCE / "run_r371_alpha_parameter_grid.py", "r380_r371")
    r371._write_variant_definitions()
    strategy_id = f"r371_industry_fade_{FADE}d_quality_h20_sl20"
    strategy_path = r371.VARIANTS / "strategies" / f"{strategy_id}.py"
    strategy_hash = sha256(strategy_path)
    membership = r371._membership()
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    meta = {
        "round": "R380",
        "strategy_id": strategy_id,
        "cell": CELL,
        "hold_days": HOLD,
        "stop_loss": STOP,
        "start": str(START),
        "end": str(END),
        "fees_pct": COSTS_2X["fees"],
        "commission_pct": COSTS_2X["commission"],
        "stamp_tax_pct": COSTS_2X["stamp"],
        "slippage_bps": COSTS_2X["slippage"],
        "matching": "open_t+1",
        "max_positions": POSITIONS["B"],
        "membership_sha256": membership_hash,
        "strategy_sha256": strategy_hash,
    }
    curve_path = CACHE / "b_hold30_2x.parquet"
    trades_path = CACHE / "b_hold30_2x.trades.json"
    stats_path = CACHE / "b_hold30_2x.stats.json"
    meta_path = CACHE / "b_hold30_2x.meta.json"
    if any(path.exists() for path in (curve_path, trades_path, stats_path, meta_path)):
        if not all(path.exists() for path in (curve_path, trades_path, stats_path, meta_path)):
            raise RuntimeError("R380 B trade cache is incomplete")
        if json.loads(meta_path.read_text(encoding="utf-8")) != meta:
            raise RuntimeError("R380 B trade cache metadata mismatch")
        return (
            _curve(curve_path),
            json.loads(trades_path.read_text(encoding="utf-8")),
            json.loads(stats_path.read_text(encoding="utf-8")),
            {"elapsed_seconds": 0.0, "cache_hit": True},
        )

    work = Path(tempfile.mkdtemp(prefix="r380_engine_data_"))
    old_data = settings.data_dir
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r380_isolation").prepare_isolated_data_root(engine_root, DATA)
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
        config = StrategyBacktestConfig(
            strategy_id=strategy_id,
            symbols=None,
            start=START,
            end=END,
            params={},
            overrides={"max_hold_days": HOLD, "stop_loss": STOP},
            matching="open_t+1",
            fees_pct=COSTS_2X["fees"],
            commission_pct=COSTS_2X["commission"],
            stamp_tax_pct=COSTS_2X["stamp"],
            slippage_bps=COSTS_2X["slippage"],
            holding_days=HOLD,
            max_positions=POSITIONS["B"],
            max_exposure_pct=1.0,
            initial_capital=INITIAL_CAPITAL,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        started = time.perf_counter()
        with r371._patched_membership(membership):
            result = service.run(config)
        if result.error:
            raise RuntimeError(f"R380 B sleeve: {result.error}")
        curve = _curve_frame(result.equity_curve)
        if curve["date"].min() != START or curve["date"].max() != END:
            raise RuntimeError("R380 B sleeve date coverage mismatch")
        frozen_b = _curve(R371_CACHE / f"{CELL}.parquet")
        if curve.height != frozen_b.height or curve["date"].to_list() != frozen_b["date"].to_list():
            raise RuntimeError("R380 B trade rerun date coverage does not match the frozen R371 curve")
        diffs = [
            abs(float(current) - float(frozen))
            for current, frozen in zip(curve["value"].to_list(), frozen_b["value"].to_list(), strict=True)
        ]
        max_diff = max(diffs, default=0.0)
        if max_diff > 1e-9:
            index = max(range(len(diffs)), key=diffs.__getitem__)
            raise RuntimeError(
                "R380 B trade rerun does not reproduce the frozen R371 curve: "
                f"max_diff={max_diff} at date={curve['date'][index]} "
                f"current={curve['value'][index]} frozen={frozen_b['value'][index]}"
            )
        trades = result.trades
        execution = dict(result.stats.get("execution") or {})
        selection = dict(result.stats.get("selection") or {})
        stats_payload = {
            "execution": execution,
            "selection": selection,
            "trade_count": len(trades),
            "stats": {
                key: result.stats.get(key)
                for key in ("total_return", "annual_return", "sharpe", "max_drawdown", "n_trades")
                if key in result.stats
            },
        }
        CACHE.mkdir(parents=True, exist_ok=True)
        curve.write_parquet(curve_path, compression="zstd")
        trades_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        stats_path.write_text(json.dumps(stats_payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        elapsed = time.perf_counter() - started
        print(f"R380 B run: trades={len(trades)} elapsed={elapsed:.1f}s", flush=True)
        return curve, trades, stats_payload, {"elapsed_seconds": elapsed, "cache_hit": False}
    finally:
        settings.data_dir = old_data
        shutil.rmtree(work, ignore_errors=True)


def portfolio_bundle(ctx: dict[str, Any], b: pl.DataFrame) -> dict[str, Any]:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r359 = ctx["r359"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    a = _curve(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
    starts = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
    c = r359.combine_cohort_curves(
        [
            _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet")
            for start in starts
        ],
        list(starts),
    )
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, DEFENSIVE_COST)
    old_112 = r112.ROUND_TRIP_COST
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = ROUND_TRIP_COST
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = ROUND_TRIP_COST
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, sleeve_weights = r344._combine_three((a, b, c), ROUND_TRIP_COST, 0.10)
        equity = r114._vol_target(risk)["curve"]
        final = r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            treasury,
            dividend,
            round_trip_cost=DEFENSIVE_COST,
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


def load_all_trades(b_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a = [{**trade, "sleeve": "A"} for trade in json.loads(A_TRADES.read_text(encoding="utf-8"))]
    b = [{**trade, "sleeve": "B"} for trade in b_trades]
    r359 = module(EVIDENCE / "run_r359_cohort_ensemble.py", "r380_r359")
    c: list[dict[str, Any]] = []
    for cohort_id, path in enumerate(C_TRADES):
        c.extend(r359.annotate_cohort_trades(json.loads(path.read_text(encoding="utf-8")), cohort_id))
    return [*a, *b, *c]


def trade_scale(bundle: dict[str, Any], trade: dict[str, Any], day: date) -> float:
    risk_dates = bundle["risk_dates"]
    if day < risk_dates[0] or day > risk_dates[-1]:
        raise ValueError(f"trade date {day} is outside the portfolio calendar")
    sleeve = str(trade["sleeve"]).lower()
    defensive_weight = weight_at(bundle["rebalances"], day, "target_equity_weight")
    sleeve_weight = weight_at(bundle["sleeve_weights"], day, f"weight_{sleeve}")
    cohort_weight = float(trade.get("cohort_initial_weight") or 1.0)
    return defensive_weight * sleeve_weight * cohort_weight


def scale_trades(trades: list[dict[str, Any]], bundle: dict[str, Any]) -> list[dict[str, Any]]:
    risk_dates = bundle["risk_dates"]
    scaled = []
    for trade in trades:
        entry = parse_day(trade["entry_date"])
        exit_day = parse_day(trade.get("exit_date") or END)
        days = [day for day in risk_dates if entry <= day <= exit_day] or [entry]
        average_scale = st.mean(trade_scale(bundle, trade, day) for day in days)
        sleeve = str(trade["sleeve"])
        nominal = INITIAL_CAPITAL / POSITIONS[sleeve]
        entry_scale = trade_scale(bundle, trade, entry)
        pnl_pct = float(trade.get("pnl_pct") or 0.0)
        entry_notional = nominal * entry_scale
        exit_notional = entry_notional * max(1.0 + pnl_pct, 0.05)
        scaled.append({
            **trade,
            "return_weight": average_scale,
            "portfolio_pnl_amount": float(trade.get("pnl_amount") or 0.0) * average_scale,
            "portfolio_entry_notional": entry_notional,
            "portfolio_exit_notional": exit_notional,
            "blocked_exit_days": int(trade.get("blocked_exit_days") or 0),
        })
    return scaled


def amount_lookup() -> dict[tuple[str, date], float]:
    r40_latest = max(path.parent.name for path in (R40 / "kline_daily_enriched").glob("date=*"))
    r40_latest_date = date.fromisoformat(r40_latest.split("=")[1])
    paths = sorted((R40 / "kline_daily_enriched").glob("date=*/part.parquet"))
    paths.extend(
        path
        for path in sorted((DATA / "kline_daily_enriched").glob("date=*/part.parquet"))
        if date.fromisoformat(path.parent.name.split("=")[1]) > r40_latest_date
    )
    frame = (
        pl.scan_parquet(paths)
        .select(["symbol", "date", "amount"])
        .collect()
        .with_columns(pl.col("date").cast(pl.Date), pl.col("amount").cast(pl.Float64))
    )
    return {
        (str(row["symbol"]), row["date"]): float(row["amount"])
        for row in frame.iter_rows(named=True)
    }


def capacity_audit(trades: list[dict[str, Any]], amounts: dict[tuple[str, date], float]) -> dict[str, Any]:
    rows = []
    missing = []
    for trade in trades:
        entry = parse_day(trade["entry_date"])
        exit_day = parse_day(trade["exit_date"])
        entry_amount = amounts.get((str(trade["symbol"]), entry))
        exit_amount = amounts.get((str(trade["symbol"]), exit_day))
        if not entry_amount or not exit_amount:
            missing.append({
                "symbol": trade["symbol"],
                "entry_date": str(entry),
                "exit_date": str(exit_day),
                "entry_amount": entry_amount,
                "exit_amount": exit_amount,
            })
        rows.append({
            "symbol": trade["symbol"],
            "entry_date": entry,
            "entry_amount": entry_amount,
            "exit_amount": exit_amount,
            "entry_notional": float(trade["portfolio_entry_notional"]),
            "exit_notional": float(trade["portfolio_exit_notional"]),
        })
    levels = []
    for capital in CAPITAL_LEVELS:
        multiplier = capital / INITIAL_CAPITAL
        participation = []
        for row in rows:
            if not row["entry_amount"] or not row["exit_amount"]:
                continue
            entry_share = row["entry_notional"] * multiplier / row["entry_amount"]
            exit_share = row["exit_notional"] * multiplier / row["exit_amount"]
            participation.append(max(entry_share, exit_share))
        p95 = quantile(participation, 0.95)
        p99 = quantile(participation, 0.99)
        p95_days = quantile([value / ATTRITION for value in participation], 0.95)
        gate = bool(
            p95 is not None and p99 is not None and p95_days is not None
            and p95 <= 0.05
            and p99 <= 0.10
            and p95_days <= 1.0
        )
        levels.append({
            "capital_cny": capital,
            "p95_participation": p95,
            "p99_participation": p99,
            "p95_days_to_liquidate_at_10pct": p95_days,
            "entry_exit_observations": len(participation),
            "above_5pct_count": sum(value > 0.05 for value in participation),
            "above_10pct_count": sum(value > 0.10 for value in participation),
            "passes": gate,
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
    if sha256(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R380 preregistration checksum mismatch")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen = json.loads(R374.read_text(encoding="utf-8"))
    expected = next(row for row in frozen["rows"] if row["cell"] == CELL)
    ctx = {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r380_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r380_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r380_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r380_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r380_r114"),
    }
    b_curve, b_trades, b_stats, run_meta = run_b_trades()
    bundle = portfolio_bundle(ctx, b_curve)
    final_stats = stats([float(value) for value in bundle["curve"]["value"].to_list()])
    reconstruction = {
        "annual_matches": math.isclose(final_stats["annual"], expected["annual_2x"], rel_tol=0.0, abs_tol=1e-9),
        "sharpe_matches": math.isclose(final_stats["sharpe"], expected["sharpe_2x"], rel_tol=0.0, abs_tol=1e-9),
        "mdd_matches": math.isclose(final_stats["mdd"], expected["mdd_2x"], rel_tol=0.0, abs_tol=1e-9),
        "metrics": final_stats,
    }
    all_trades = load_all_trades(b_trades)
    scaled = scale_trades(all_trades, bundle)
    industry = industry_map()
    r239 = module(ROOT / "scripts/backtest_r239_concentration_audit.py", "r380_r239")
    full = r239._aggregate(scaled, industry)
    recent_trades = [trade for trade in scaled if parse_day(trade["entry_date"]) >= RECENT_START]
    recent = r239._aggregate(recent_trades, industry)
    b_scaled = [trade for trade in scaled if trade["sleeve"] == "B"]
    b_full = r239._aggregate(b_scaled, industry)
    recent_positive = sum(row["pnl_amount"] > 0 for row in recent["per_symbol"])
    execution = execution_audit(b_stats, b_trades)
    amounts = amount_lookup()
    capacity = capacity_audit(scaled, amounts)
    gates = prereg["gates"]
    concentration_checks = {
        "distinct_symbols": full["distinct_symbols"] >= gates["concentration"]["distinct_symbols_min"],
        "top1_gross": full["top1_gross_share"] is not None and full["top1_gross_share"] <= gates["concentration"]["top1_gross_profit_share_max"],
        "top3_gross": full["top3_gross_share"] is not None and full["top3_gross_share"] <= gates["concentration"]["top3_gross_profit_share_max"],
        "top5_gross": full["top5_gross_share"] is not None and full["top5_gross_share"] <= gates["concentration"]["top5_gross_profit_share_max"],
        "top_industry_gross": full["top_industry_gross_share"] is not None and full["top_industry_gross_share"] <= gates["concentration"]["top_industry_gross_profit_share_max"],
        "max_symbol_loss": full["max_symbol_loss_to_gross"] is not None and full["max_symbol_loss_to_gross"] <= gates["concentration"]["max_single_symbol_loss_to_gross_profit_max"],
        "recent_positive_symbols": recent_positive >= gates["concentration"]["recent_positive_symbols_min"],
    }
    execution_checks = {
        "hard_entry_miss_rate": execution["hard_entry_miss_rate"] is not None and execution["hard_entry_miss_rate"] <= gates["execution"]["hard_entry_miss_rate_max"],
        "hard_exit_miss_rate": execution["hard_exit_miss_rate"] is not None and execution["hard_exit_miss_rate"] <= gates["execution"]["hard_exit_miss_rate_max"],
        "blocked_exit_trade_share": execution["blocked_exit_trade_share"] is not None and execution["blocked_exit_trade_share"] <= gates["execution"]["blocked_exit_trade_share_max"],
        "max_blocked_exit_days": execution["max_blocked_exit_days"] <= gates["execution"]["max_blocked_exit_days_per_trade_max"],
    }
    capacity_checks = {
        "one_hundred_million": capacity["one_hundred_million_gate_passes"],
        "no_missing_daily_amount": len(capacity["missing_daily_amount"]) == 0,
    }
    curve_checks = {
        "annual": reconstruction["annual_matches"],
        "sharpe": reconstruction["sharpe_matches"],
        "mdd": reconstruction["mdd_matches"],
    }
    all_checks = {
        "curve": all(curve_checks.values()),
        "concentration": all(concentration_checks.values()),
        "execution": all(execution_checks.values()),
        "capacity": all(capacity_checks.values()),
    }
    return {
        "round": "R380",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "cell": CELL,
        "run": run_meta,
        "reconstruction": {**reconstruction, "checks": curve_checks},
        "trade_counts": {
            "A": sum(trade["sleeve"] == "A" for trade in scaled),
            "B": sum(trade["sleeve"] == "B" for trade in scaled),
            "C": sum(trade["sleeve"] == "C" for trade in scaled),
            "total": len(scaled),
        },
        "concentration_full": {k: v for k, v in full.items() if k != "per_symbol"},
        "concentration_recent": {k: v for k, v in recent.items() if k != "per_symbol"},
        "concentration_b_sleeve": {k: v for k, v in b_full.items() if k != "per_symbol"},
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
        "all_checks": all_checks,
        "all_pass": all(all_checks.values()),
        "trades": scaled,
        "decision": {
            "trade_level_audit_pass": all(all_checks.values()),
            "status": "candidate_for_paper_trading_review" if all(all_checks.values()) else "research_only_trade_audit_failed",
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
        "source_r378_sha256": sha256(R378),
    }


def markdown(report: dict[str, Any]) -> str:
    full = report["concentration_full"]
    execution = report["execution"]
    capacity = report["capacity"]
    cap100 = next(row for row in capacity["capital_levels"] if row["capital_cny"] == 100_000_000)
    lines = [
        "# R380 · hold-30 中心交易级、执行与容量审计",
        "",
        f"冻结 `{report['cell']}`，2× 成本，交易总数 {report['trade_counts']['total']}（A/B/C = {report['trade_counts']['A']}/{report['trade_counts']['B']}/{report['trade_counts']['C']}）。",
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
        f"- 容量最大通过档位：{capacity['maximum_passing_level_cny']:,} 元。" if capacity["maximum_passing_level_cny"] else "- 没有容量档位通过。",
        f"- 2× 组合重建：{report['reconstruction']['checks']}。",
        f"- 综合判定：{'通过' if report['all_pass'] else '未通过'}，状态 `{report['decision']['status']}`。",
        "",
        "- A/C 容量名义金额由等权名义仓位重建；B 袖使用本次完整成交记录。",
        "- 日成交额只是可执行性代理，缺少盘口深度和冲击成本曲线。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R380 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps({
        "all_checks": report["all_checks"],
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
