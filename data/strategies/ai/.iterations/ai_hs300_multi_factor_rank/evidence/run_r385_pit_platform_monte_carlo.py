"""R385: current-PIT platform and paired Monte Carlo revalidation.

All alpha cells are rerun with the R381 repaired membership and effective-date
industry frame. The script refuses to read portfolio performance until every
input and the current engine hash have been verified.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
import shutil
import statistics as st
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from app.factor_research.evidence_guard import formal_run

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
R40 = RESEARCH / "r40_engine_data"
PREREG = EVIDENCE / "r385_preregistration.json"
R381_PIT_AUDIT = EVIDENCE / "r381_pit_data_audit.json"
R381_INPUT_AUDIT = EVIDENCE / "r381_input_audit.json"
R381_MEMBERSHIP = RESEARCH / "r381_membership/daily_snapshots.parquet"
R381_EXT_FRAME = RESEARCH / "r381_pit_industry/alt_hs300_required_fields.parquet"
OUTPUT = EVIDENCE / "r385_pit_platform_monte_carlo.json"
RESULT = EVIDENCE / "r385_result.md"
CACHE = RESEARCH / "r385_pit_cache"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
SEALED_START = date(2025, 5, 6)
RECENT_START = date(2025, 11, 5)
ROLLING_STARTS = (
    date(2022, 5, 5),
    date(2023, 5, 5),
    date(2024, 5, 5),
    date(2025, 5, 5),
)
COHORT_STARTS = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
ROLLING_RETURNS = 252
WALKFORWARD_TRAILING = 252
WALKFORWARD_FORWARD = 252
WALKFORWARD_STEP = 21
INITIAL_CAPITAL = 1_000_000.0
A_STRATEGY = "ai_hs300_macro_gate_quality"
C_STRATEGY = "ai_hs300_quality_momentum_broad_h60"
FADES = (3, 5, 10)
HOLDS = (10, 20, 30)
STOPS = (-0.15, -0.20, -0.25)
CENTRE_CELL = "fade05_h30_sl20"
CONTROL_CELL = "fade05_h20_sl20"
PLATFORM_CELLS = (
    "fade03_h30_sl15",
    "fade05_h30_sl15",
    "fade05_h30_sl20",
    "fade05_h30_sl25",
    "fade10_h30_sl15",
    "fade10_h30_sl20",
    "fade10_h30_sl25",
)
DEFAULT_PARAMETERS = {
    "c_satellite_cap": 0.10,
    "equity_target_vol": 0.15,
    "overlay_lookback_days": 60,
    "overlay_target_vol": 0.15,
    "overlay_equity_weight_max": 1.0 / 3.0,
}
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
    "5x": {
        "stock": {"fees": 0.001, "commission": 0.001, "stamp": 0.00125, "slippage": 25.0},
        "portfolio_turnover": 0.013,
        "defensive_turnover": 0.0025,
    },
}
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


def cell_slug(fade: int, hold: int, stop: float) -> str:
    return f"fade{fade:02d}_h{hold:02d}_sl{round(abs(stop) * 100):02d}"


def cell_params(cell: str) -> tuple[int, int, float]:
    parts = cell.split("_")
    return int(parts[0][4:]), int(parts[1][1:]), -int(parts[2][2:]) / 100.0


def grid_cells() -> tuple[str, ...]:
    return tuple(cell_slug(fade, hold, stop) for fade in FADES for hold in HOLDS for stop in STOPS)


def latin_hypercube(samples: int, dimensions: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in range(dimensions):
        strata = list(range(samples))
        rng.shuffle(strata)
        columns.append([(strata[index] + rng.random()) / samples for index in range(samples)])
    return [[columns[column][row] for column in range(dimensions)] for row in range(samples)]


def verify_inputs() -> dict[str, Any]:
    actual_prereg = sha256(PREREG)
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    input_audit = json.loads(R381_INPUT_AUDIT.read_text(encoding="utf-8"))
    pit_audit = json.loads(R381_PIT_AUDIT.read_text(encoding="utf-8"))
    bound = {
        "r381_input_audit": R381_INPUT_AUDIT,
        "r381_pit_data_audit": R381_PIT_AUDIT,
        "membership": R381_MEMBERSHIP,
        "extension_frame": R381_EXT_FRAME,
    }
    checks: dict[str, bool] = {}
    actual: dict[str, str] = {}
    for name, path in bound.items():
        actual[name] = sha256(path)
    prereg_sidecar = PREREG.with_name(PREREG.name + ".sha256")
    checks["preregistration_sidecar"] = (
        prereg_sidecar.read_text(encoding="ascii").split()[0] == actual_prereg
    )
    for name in ("r381_input_audit", "r381_pit_data_audit"):
        path = bound[name]
        sidecar = path.with_name(path.name + ".sha256")
        checks[f"{name}_sidecar"] = (
            sidecar.read_text(encoding="ascii").split()[0] == actual[name]
        )
    checks["membership_binding"] = (
        actual["membership"] == input_audit["membership"]["repaired_sha256"]
    )
    checks["extension_binding"] = (
        actual["extension_frame"] == input_audit["extension_frame"]["sha256"]
    )
    checks["data_integrity"] = bool(input_audit.get("data_integrity_pass"))
    checks["r381_portfolio_pass"] = bool(pit_audit.get("all_pass"))
    checks["r381_status"] = (
        pit_audit.get("decision", {}).get("status") == "candidate_for_paper_trading_review"
    )
    current_engine = sha256(ROOT / "backend/app/backtest/engine.py")
    checks["engine_binding"] = (
        current_engine == pit_audit["engine_identity"]["engine_sha256"]
    )
    checks["preregistration"] = (
        prereg.get("round") == "R385"
        and len(prereg.get("frozen_design", {}).get("platform_cells", [])) == 7
    )
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise RuntimeError(f"R385 input lineage gate failed: {failed}")
    return {
        "checks": checks,
        "actual_sha256": {**actual, "engine": current_engine},
        "preregistration": prereg,
        "input_audit": input_audit,
        "pit_audit": pit_audit,
    }


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def _read_curve(path: Path) -> pl.DataFrame:
    frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    if frame.columns != ["date", "value"]:
        raise ValueError(f"unexpected curve schema: {path}")
    return frame


def _flat_curve(reference: pl.DataFrame) -> pl.DataFrame:
    return reference.select("date").with_columns(pl.lit(1.0).alias("value"))


def stats(values: list[float]) -> dict[str, float | int]:
    if len(values) < 2:
        raise ValueError("equity curve requires at least two observations")
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
    return {
        **stats([float(value) for value in part["value"].to_list()]),
        "start": str(part["date"][0]),
        "end": str(part["date"][-1]),
    }


def year_returns(frame: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    result: dict[str, float] = {}
    previous = None
    for year in sorted(grouped):
        values = grouped[year]
        base = previous if previous is not None else values[0]
        result[str(year)] = values[-1] / base - 1.0
        previous = values[-1]
    return result


def rolling_positive(frame: pl.DataFrame) -> dict[str, Any]:
    windows = []
    for start in ROLLING_STARTS:
        part = frame.filter(pl.col("date") >= start).sort("date").head(ROLLING_RETURNS + 1)
        if part.height != ROLLING_RETURNS + 1:
            raise ValueError(f"rolling window from {start} is incomplete")
        values = part["value"].to_list()
        windows.append({"start": str(start), "return": float(values[-1] / values[0] - 1.0)})
    return {
        "window_count": len(windows),
        "positive_count": sum(window["return"] > 0 for window in windows),
        "windows": windows,
    }


def curve_metrics(frame: pl.DataFrame) -> dict[str, Any]:
    return {
        "full": stats([float(value) for value in frame["value"].to_list()]),
        "yearly": year_returns(frame),
        "recent": segment(frame, RECENT_START),
        "sealed": segment(frame, SEALED_START),
        "rolling": rolling_positive(frame),
    }


def _cache_key(kind: str, cost: str, label: str) -> str:
    return f"{kind}_{cost}_{label}"


def _cache_meta(
    *,
    kind: str,
    strategy_id: str,
    start: date,
    cost: str,
    max_positions: int,
    overrides: dict[str, Any],
    hold: int | None,
    initial_capital: float,
    strategy_hash: str,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    scenario = SCENARIOS[cost]
    return {
        "round": "R385",
        "kind": kind,
        "strategy_id": strategy_id,
        "start": str(start),
        "end": str(END),
        "cost": cost,
        "stock": scenario["stock"],
        "portfolio_turnover": scenario["portfolio_turnover"],
        "defensive_turnover": scenario["defensive_turnover"],
        "matching": "open_t+1",
        "max_positions": max_positions,
        "overrides": overrides,
        "hold": hold,
        "initial_capital": initial_capital,
        "strategy_sha256": strategy_hash,
        **provenance,
    }


def run_current_pit_engine() -> dict[str, Any]:
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    r371 = module(EVIDENCE / "run_r371_alpha_parameter_grid.py", "r385_r371")
    definitions = r371._write_variant_definitions()
    r381 = module(EVIDENCE / "run_r381_pit_data_audit.py", "r385_r381")
    membership = r381.membership_for_engine()
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    ext_frame = pl.read_parquet(R381_EXT_FRAME).with_columns(pl.col("date").cast(pl.Date))
    engine_hash = sha256(ROOT / "backend/app/backtest/engine.py")
    strategy_paths = {
        "A": DATA / "strategies/ai" / f"{A_STRATEGY}.py",
        "C": DATA / "strategies/ai" / f"{C_STRATEGY}.py",
        **{
            f"B_{fade}": ROOT / definitions["strategies"][str(fade)]["path"]
            for fade in FADES
        },
    }
    provenance = {
        "membership_sha256": membership_hash,
        "extension_frame_sha256": sha256(R381_EXT_FRAME),
        "engine_sha256": engine_hash,
        "r371_runner_sha256": sha256(EVIDENCE / "run_r371_alpha_parameter_grid.py"),
        "variant_definitions": definitions,
    }
    outputs: dict[str, tuple[pl.DataFrame, dict[str, Any]]] = {}
    work = Path(tempfile.mkdtemp(prefix="r385_engine_data_"))
    old_data = settings.data_dir
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r385_isolation").prepare_isolated_data_root(
            engine_root, DATA
        )
        r381.write_ext_timeseries(engine_root, ext_frame)
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
        service = StrategyBacktestService(
            BacktestEngine(KlineRepository(DataStore(engine_root))), engine
        )

        def run_one(
            kind: str,
            strategy_id: str,
            start: date,
            cost: str,
            max_positions: int,
            *,
            label: str,
            strategy_path: Path,
            overrides: dict[str, Any] | None = None,
            hold: int | None = None,
            initial_capital: float = INITIAL_CAPITAL,
        ) -> tuple[pl.DataFrame, dict[str, Any]]:
            effective_overrides = overrides or {}
            meta = _cache_meta(
                kind=kind,
                strategy_id=strategy_id,
                start=start,
                cost=cost,
                max_positions=max_positions,
                overrides=effective_overrides,
                hold=hold,
                initial_capital=initial_capital,
                strategy_hash=sha256(strategy_path),
                provenance=provenance,
            )
            name = _cache_key(kind, cost, label)
            parquet = CACHE / f"{name}.parquet"
            sidecar = CACHE / f"{name}.json"
            if parquet.exists() and sidecar.exists():
                cached = json.loads(sidecar.read_text(encoding="utf-8"))
                if cached.get("meta") == meta:
                    cached_curve = _read_curve(parquet)
                    if (
                        cached_curve.height >= 2
                        and cached_curve["date"].min() == start
                        and cached_curve["date"].max() == END
                    ):
                        print(f"R385 cache hit {name}", flush=True)
                        return cached_curve, {**cached["run"], "cache_hit": True}
                    print(
                        f"R385 cache invalid {name}: rows={cached_curve.height} "
                        f"range={cached_curve['date'].min()}..{cached_curve['date'].max()}",
                        flush=True,
                    )
            stock = SCENARIOS[cost]["stock"]
            config = StrategyBacktestConfig(
                strategy_id=strategy_id,
                symbols=None,
                start=start,
                end=END,
                params={},
                overrides=effective_overrides,
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
                result = formal_run(service, config)
            if result.error:
                raise RuntimeError(f"R385 {kind} {label} {cost}: {result.error}")
            curve = _curve_frame(result.equity_curve)
            if curve["date"].min() != start or curve["date"].max() != END:
                raise RuntimeError(f"R385 {kind} {label} {cost}: date coverage mismatch")
            elapsed = time.perf_counter() - started
            run = {
                "elapsed_seconds": elapsed,
                "cache_hit": False,
                "n_trades": len(result.trades),
            }
            curve.write_parquet(parquet, compression="zstd")
            sidecar.write_text(
                json.dumps(
                    {"meta": meta, "run": run},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                f"R385 ran {kind} {label} {cost}: trades={len(result.trades)} elapsed={elapsed:.1f}s",
                flush=True,
            )
            return curve, run

        for cost in ("2x", "3x"):
            outputs[f"A_{cost}"], _ = run_one(
                "A",
                A_STRATEGY,
                START,
                cost,
                10,
                label="A",
                strategy_path=strategy_paths["A"],
            )
            for index, cohort_start in enumerate(COHORT_STARTS):
                outputs[f"C_{cost}_{index}"], _ = run_one(
                    "C",
                    C_STRATEGY,
                    cohort_start,
                    cost,
                    50,
                    label=f"C{index}",
                    strategy_path=strategy_paths["C"],
                    initial_capital=INITIAL_CAPITAL / 3.0,
                )
            for cell in grid_cells():
                fade, hold, stop = cell_params(cell)
                strategy_id = definitions["strategies"][str(fade)]["id"]
                outputs[f"B_{cost}_{cell}"], _ = run_one(
                    "B",
                    strategy_id,
                    START,
                    cost,
                    10,
                    label=cell,
                    strategy_path=strategy_paths[f"B_{fade}"],
                    overrides={"max_hold_days": hold, "stop_loss": stop},
                    hold=hold,
                )

        cost = "5x"
        outputs[f"A_{cost}"], _ = run_one(
            "A",
            A_STRATEGY,
            START,
            cost,
            10,
            label="A",
            strategy_path=strategy_paths["A"],
        )
        for index, cohort_start in enumerate(COHORT_STARTS):
            outputs[f"C_{cost}_{index}"], _ = run_one(
                "C",
                C_STRATEGY,
                cohort_start,
                cost,
                50,
                label=f"C{index}",
                strategy_path=strategy_paths["C"],
                initial_capital=INITIAL_CAPITAL / 3.0,
            )
        fade, hold, stop = cell_params(CENTRE_CELL)
        outputs[f"B_{cost}_{CENTRE_CELL}"], _ = run_one(
            "B",
            definitions["strategies"][str(fade)]["id"],
            START,
            cost,
            10,
            label=CENTRE_CELL,
            strategy_path=strategy_paths[f"B_{fade}"],
            overrides={"max_hold_days": hold, "stop_loss": stop},
            hold=hold,
        )
        return {
            "outputs": outputs,
            "provenance": provenance,
            "membership_sha256": membership_hash,
            "engine_sha256": engine_hash,
            "variant_sha256": {
                str(fade): sha256(strategy_paths[f"B_{fade}"]) for fade in FADES
            },
        }
    finally:
        settings.data_dir = old_data
        pipeline.invalidate_custom_signals()
        shutil.rmtree(work, ignore_errors=True)


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r385_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r385_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r385_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r385_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r385_r114"),
    }


def combine_c(outputs: dict[str, tuple[pl.DataFrame, dict[str, Any]]], cost: str, ctx: dict[str, Any]) -> pl.DataFrame:
    r359 = ctx["r359"]
    curves = [outputs[f"C_{cost}_{index}"] for index in range(3)]
    return r359.combine_cohort_curves(curves, list(COHORT_STARTS))


def prepare_assets(ctx: dict[str, Any]) -> dict[str, Any]:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    gold = r331.asset_frame("gold", "518880.SH")
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    return {
        "gold": gold,
        "treasury": treasury,
        "dividend": dividend,
        "gold_proxy": {
            cost: r344.gold_trend_proxy(
                gold,
                treasury,
                dividend,
                scenario["defensive_turnover"],
            )
            for cost, scenario in SCENARIOS.items()
        },
    }


def compose_portfolio(
    ctx: dict[str, Any],
    assets: dict[str, Any],
    a: pl.DataFrame,
    b: pl.DataFrame,
    c: pl.DataFrame,
    cost: str,
    parameters: dict[str, Any],
    *,
    mode: str = "full",
) -> dict[str, Any]:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    scenario = SCENARIOS[cost]
    effective_c = _flat_curve(b) if mode == "without_C_satellite" else c
    c_cap = 0.0 if mode == "without_C_satellite" else float(parameters["c_satellite_cap"])
    old_112 = r112.ROUND_TRIP_COST
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r114.TARGET_VOL = float(parameters["equity_target_vol"])
        r114.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r331.LOOKBACK = int(parameters["overlay_lookback_days"])
        r331.TARGET_VOL = float(parameters["overlay_target_vol"])
        r331.EQUITY_WEIGHT_MAX = float(parameters["overlay_equity_weight_max"])
        risk, sleeve_weights = r344._combine_three(
            (a, b, effective_c),
            scenario["portfolio_turnover"],
            c_cap,
        )
        equity = (
            risk
            if mode == "without_equity_volatility_target"
            else r114._vol_target(risk)["curve"]
        )
        if mode == "without_defensive_overlay":
            return {
                "curve": equity,
                "sleeve_weights": sleeve_weights,
                "weight_summary": None,
                "rebalances": [],
            }
        final = r331.build_three_asset_bucket(
            equity,
            assets["gold_proxy"][cost],
            assets["treasury"],
            assets["dividend"],
            round_trip_cost=scenario["defensive_turnover"],
        )
        return {
            "curve": final["curve"],
            "sleeve_weights": sleeve_weights,
            "weight_summary": final.get("weight_summary"),
            "rebalances": final.get("rebalances", []),
        }
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def evaluate_platform(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) != len(PLATFORM_CELLS):
        raise ValueError("R385 platform must contain exactly seven cells")
    if {row["cell"] for row in rows} != set(PLATFORM_CELLS):
        raise ValueError("R385 platform cell set mismatch")
    passed = all(
        bool(row["pass_2x"] and row["pass_3x"] and row["rolling_2x_positive"] >= 3)
        for row in rows
    )
    highest = max(rows, key=lambda row: float(row["annual_2x"]))["cell"]
    return {
        "platform_pass": passed,
        "selected_cell": CENTRE_CELL if passed else None,
        "highest_annual_cell": highest,
        "selected_cell_is_highest_annual": highest == CENTRE_CELL,
    }


def evaluate_five_x(metrics: dict[str, float]) -> dict[str, Any]:
    warning = bool(metrics["annual"] < 0.05 or metrics["sharpe"] < 0.8 or metrics["mdd"] < -0.25)
    return {"warning": warning, "hard_rejection": False}


def evaluate_grid(rows: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any]:
    if len(rows) != 27 or {row["cell"] for row in rows} != set(grid_cells()):
        raise ValueError("R385 broad grid must contain exactly the preregistered 27 cells")
    passed = sum(bool(row["pass_2x"]) for row in rows)
    pass_rate = passed / len(rows)
    marginals: dict[str, dict[str, float]] = {}
    for key in ("fade_days", "hold_days", "stop_loss"):
        grouped: dict[str, list[bool]] = {}
        for row in rows:
            grouped.setdefault(str(row[key]), []).append(bool(row["pass_2x"]))
        marginals[f"marginal_{key}"] = {
            value: sum(values) / len(values) for value, values in sorted(grouped.items())
        }
    minimum = min(rate for group in marginals.values() for rate in group.values())
    thresholds = prereg["evaluation"]["broad_grid"]
    return {
        "pass_count_2x": passed,
        "cell_count": len(rows),
        "pass_rate_2x": pass_rate,
        **marginals,
        "minimum_marginal_pass_rate_2x": minimum,
        "broad_grid_pass": bool(
            pass_rate >= thresholds["pass_rate_2x_min"]
            and minimum >= thresholds["each_parameter_level_pass_rate_2x_min"]
        ),
    }


def evaluate_monte_carlo(rows: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any]:
    thresholds = prereg["evaluation"]["monte_carlo"]
    full_gate = thresholds["full_v2_gate"]
    sealed_gate = thresholds["sealed_gate"]
    full_pass = []
    sealed_pass = []
    advantage_pass = []
    for row in rows:
        centre = row["centre"]
        full = centre["full"]
        recent = centre["recent"]
        sealed = centre["sealed"]
        full_pass.append(bool(
            full["annual"] >= full_gate["annual_min"]
            and full["sharpe"] >= full_gate["sharpe_min"]
            and full["mdd"] >= full_gate["mdd_min"]
            and centre["yearly"].get("2022", -1.0) >= full_gate["year_2022_min"]
            and centre["yearly"].get("2023", -1.0) >= full_gate["year_2023_min"]
            and recent["total_return"] > full_gate["recent_total_return_min_exclusive"]
        ))
        sealed_pass.append(bool(
            sealed["total_return"] > sealed_gate["total_return_min_exclusive"]
            and sealed["sharpe"] >= sealed_gate["sharpe_min"]
            and sealed["mdd"] >= sealed_gate["mdd_min"]
        ))
        advantage_pass.append(
            float(row["sealed_advantage"]) > thresholds["centre_paired_sealed_advantage_min_exclusive"]
        )
    denominator = len(rows)
    if not denominator:
        raise ValueError("R385 Monte Carlo cannot be empty")
    rates = {
        "full_pass_rate": sum(full_pass) / denominator,
        "sealed_pass_rate": sum(sealed_pass) / denominator,
        "paired_sealed_advantage_rate": sum(advantage_pass) / denominator,
    }
    return {
        "sample_count": denominator,
        "full_pass_count": sum(full_pass),
        "sealed_pass_count": sum(sealed_pass),
        "paired_sealed_advantage_count": sum(advantage_pass),
        "failed_full_samples": [
            row.get("sample", index)
            for index, (row, passed) in enumerate(zip(rows, full_pass, strict=True))
            if not passed
        ],
        "failed_sealed_samples": [
            row.get("sample", index)
            for index, (row, passed) in enumerate(zip(rows, sealed_pass, strict=True))
            if not passed
        ],
        "failed_advantage_samples": [
            row.get("sample", index)
            for index, (row, passed) in enumerate(zip(rows, advantage_pass, strict=True))
            if not passed
        ],
        **rates,
        "parameter_plateau_pass": bool(
            rates["full_pass_rate"] >= thresholds["full_pass_rate_min"]
            and rates["sealed_pass_rate"] >= thresholds["sealed_pass_rate_min"]
            and rates["paired_sealed_advantage_rate"]
            >= thresholds["paired_sealed_advantage_rate_min"]
        ),
    }


def exposures_within_limit(values: list[float]) -> bool:
    return bool(all(float(value) <= 1.0 + 1e-12 for value in values))


def _gate_2x(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    gate = prereg["evaluation"]["2x_point_gate"]
    return bool(
        metrics["full"]["annual"] >= gate["annual_min"]
        and metrics["full"]["sharpe"] >= gate["sharpe_min"]
        and metrics["full"]["mdd"] >= gate["mdd_min"]
        and metrics["yearly"].get("2022", -1.0) >= gate["year_2022_min"]
        and metrics["yearly"].get("2023", -1.0) >= gate["year_2023_min"]
        and metrics["recent"]["total_return"] > gate["recent_total_return_min_exclusive"]
        and metrics["rolling"]["positive_count"] >= gate["rolling_positive_count_min"]
    )


def _gate_3x(metrics: dict[str, Any], prereg: dict[str, Any]) -> bool:
    gate = prereg["evaluation"]["3x_point_gate"]
    return bool(
        metrics["full"]["annual"] >= gate["annual_min"]
        and metrics["full"]["sharpe"] >= gate["sharpe_min"]
        and metrics["full"]["mdd"] >= gate["mdd_min"]
        and metrics["rolling"]["positive_count"] >= gate["rolling_positive_count_min"]
    )


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(fraction * len(ordered))))]


def sample_parameters(prereg: dict[str, Any]) -> list[dict[str, Any]]:
    design = prereg["evaluation"]["monte_carlo"]
    ranges = design["ranges"]
    units = latin_hypercube(design["samples"], len(ranges), design["seed"])
    output = []
    for index, unit in enumerate(units):
        c_cap = ranges["c_satellite_cap"][0] + unit[0] * (
            ranges["c_satellite_cap"][1] - ranges["c_satellite_cap"][0]
        )
        equity_target = ranges["equity_target_vol"][0] + unit[1] * (
            ranges["equity_target_vol"][1] - ranges["equity_target_vol"][0]
        )
        lookback = round(
            ranges["overlay_lookback_days"][0]
            + unit[2] * (
                ranges["overlay_lookback_days"][1] - ranges["overlay_lookback_days"][0]
            )
        )
        overlay_target = ranges["overlay_target_vol"][0] + unit[3] * (
            ranges["overlay_target_vol"][1] - ranges["overlay_target_vol"][0]
        )
        equity_weight_max = ranges["overlay_equity_weight_max"][0] + unit[4] * (
            ranges["overlay_equity_weight_max"][1]
            - ranges["overlay_equity_weight_max"][0]
        )
        output.append({
            "sample": index,
            "parameters": {
                "c_satellite_cap": c_cap,
                "equity_target_vol": equity_target,
                "overlay_lookback_days": lookback,
                "overlay_target_vol": overlay_target,
                "overlay_equity_weight_max": equity_weight_max,
            },
        })
    return output


def _curve_stats(values: list[float]) -> dict[str, float | int]:
    return stats(values)


def _axis_neighbours(cell: str) -> tuple[str, ...]:
    fade, hold, stop = cell_params(cell)
    fade_index, hold_index, stop_index = FADES.index(fade), HOLDS.index(hold), STOPS.index(stop)
    output = []
    for delta in (-1, 1):
        if 0 <= fade_index + delta < len(FADES):
            output.append(cell_slug(FADES[fade_index + delta], hold, stop))
        if 0 <= hold_index + delta < len(HOLDS):
            output.append(cell_slug(fade, HOLDS[hold_index + delta], stop))
        if 0 <= stop_index + delta < len(STOPS):
            output.append(cell_slug(fade, hold, STOPS[stop_index + delta]))
    return tuple(dict.fromkeys(output))


def _parameter_distance(left: str, right: str) -> int:
    left_fade, left_hold, left_stop = cell_params(left)
    right_fade, right_hold, right_stop = cell_params(right)
    return (
        abs(FADES.index(left_fade) - FADES.index(right_fade))
        + abs(HOLDS.index(left_hold) - HOLDS.index(right_hold))
        + abs(STOPS.index(left_stop) - STOPS.index(right_stop))
    )


def _trailing_metrics(curves: dict[str, pl.DataFrame], decision: date) -> dict[str, dict[str, Any]]:
    output = {}
    for cell, curve in curves.items():
        window = curve.filter(pl.col("date") <= decision).sort("date")
        if window.height < WALKFORWARD_TRAILING + 1:
            raise ValueError(f"{cell} has fewer than {WALKFORWARD_TRAILING} trailing returns")
        values = [float(value) for value in window["value"].tail(WALKFORWARD_TRAILING + 1).to_list()]
        output[cell] = _curve_stats(values)
    return output


def _trailing_best(metrics: dict[str, dict[str, Any]]) -> str:
    return max(
        sorted(metrics),
        key=lambda cell: (metrics[cell]["total_return"], metrics[cell]["sharpe"], cell),
    )


def _platform_medoid(metrics: dict[str, dict[str, Any]]) -> str:
    passing = [
        cell for cell in sorted(metrics)
        if metrics[cell]["annual"] >= 0.08
        and metrics[cell]["sharpe"] >= 1.0
        and metrics[cell]["mdd"] >= -0.20
    ]
    if len(passing) < 5:
        return CENTRE_CELL

    def score(cell: str) -> tuple[int, float, str]:
        spread = sum(_parameter_distance(cell, other) for other in passing)
        return spread, -float(metrics[cell]["sharpe"]), cell

    return min(passing, key=score)


def _maximin_neighbour(metrics: dict[str, dict[str, Any]]) -> str:
    def score(cell: str) -> tuple[float, float, str]:
        neighbours = [_axis_neighbours(cell)]
        floors = [
            float(metrics[other]["sharpe"])
            for group in neighbours
            for other in group
            if other in metrics
        ]
        floor = min(floors) if floors else float(metrics[cell]["sharpe"])
        return floor, float(metrics[cell]["sharpe"]), cell

    return max(sorted(metrics), key=score)


def _forward_return_and_mdd(curve: pl.DataFrame, decision: date) -> dict[str, float]:
    window = curve.filter(pl.col("date") >= decision).sort("date").head(WALKFORWARD_FORWARD + 1)
    if window.height != WALKFORWARD_FORWARD + 1:
        raise ValueError(f"forward window from {decision} is incomplete")
    values = [float(value) for value in window["value"].to_list()]
    return {
        "return": values[-1] / values[0] - 1.0,
        "mdd": stats(values)["mdd"],
    }


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    output = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and values[ordered[end + 1]] == values[ordered[index]]:
            end += 1
        rank = (index + end) / 2.0 + 1.0
        for position in ordered[index:end + 1]:
            output[position] = rank
        index = end + 1
    return output


def _spearman(left: list[float], right: list[float]) -> float:
    left_rank = _rank(left)
    right_rank = _rank(right)
    left_mean = st.mean(left_rank)
    right_mean = st.mean(right_rank)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left_rank, right_rank, strict=True)
    )
    denominator = math.sqrt(
        sum((a - left_mean) ** 2 for a in left_rank)
        * sum((b - right_mean) ** 2 for b in right_rank)
    )
    return numerator / denominator if denominator else 0.0


def run_walkforward(curves: dict[str, pl.DataFrame]) -> dict[str, Any]:
    sessions = curves[CENTRE_CELL]["date"].to_list()
    points = []
    for index in range(WALKFORWARD_TRAILING, len(sessions) - WALKFORWARD_FORWARD, WALKFORWARD_STEP):
        points.append(sessions[index])
    if not points:
        raise ValueError("R385 walk-forward has no decision points")
    records = []
    for decision in points:
        trailing = _trailing_metrics(curves, decision)
        selected = {
            "P1_trailing_best": _trailing_best(trailing),
            "P3_platform_medoid": _platform_medoid(trailing),
            "P4_maximin_neighbour": _maximin_neighbour(trailing),
            "P5_fixed_centre": CENTRE_CELL,
        }
        forward = {
            cell: _forward_return_and_mdd(curve, decision) for cell, curve in curves.items()
        }
        records.append({
            "decision": str(decision),
            "selected": selected,
            "forward": forward,
            "rank_correlation": _spearman(
                [trailing[cell]["total_return"] for cell in sorted(curves)],
                [forward[cell]["return"] for cell in sorted(curves)],
            ),
        })
    policies = {}
    for name in records[0]["selected"]:
        returns = []
        mdds = []
        for record in records:
            cell = record["selected"][name]
            returns.append(record["forward"][cell]["return"])
            mdds.append(record["forward"][cell]["mdd"])
        policies[name] = {
            "decisions": len(records),
            "mean_forward_return": st.mean(returns),
            "worst_forward_return": min(returns),
            "negative_windows": sum(value < 0 for value in returns),
            "mean_forward_mdd": st.mean(mdds),
            "worst_forward_mdd": min(mdds),
        }
    trailing = policies["P1_trailing_best"]
    medoid = policies["P3_platform_medoid"]
    fixed = policies["P5_fixed_centre"]
    medoid_beats_both = bool(
        medoid["mean_forward_return"] > max(trailing["mean_forward_return"], fixed["mean_forward_return"])
        and medoid["worst_forward_return"]
        >= max(trailing["worst_forward_return"], fixed["worst_forward_return"])
    )
    fixed_dominates = bool(
        fixed["mean_forward_return"] >= max(
            policies[name]["mean_forward_return"]
            for name in policies
            if name != "P5_fixed_centre"
        )
        and fixed["worst_forward_return"] >= max(
            policies[name]["worst_forward_return"]
            for name in policies
            if name != "P5_fixed_centre"
        )
    )
    return {
        "decision_points": len(records),
        "trailing_returns": WALKFORWARD_TRAILING,
        "forward_returns": WALKFORWARD_FORWARD,
        "policies": policies,
        "decision": {
            "medoid_beats_both_on_mean_and_worst": medoid_beats_both,
            "fixed_centre_dominates": fixed_dominates,
            "verdict": (
                "platform_selection_supported"
                if medoid_beats_both
                else (
                    "no_ex_ante_selector_adds_value_fixed_centre_remains_default"
                    if fixed_dominates
                    else "platform_claim_limited_to_parameter_robustness"
                )
            ),
            "selection_changed": False,
        },
        "records": records,
    }


def _reconcile_r381(current: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    specs = {
        "center_2x_full_annual": ("center_2x", "full", "annual"),
        "center_2x_full_sharpe": ("center_2x", "full", "sharpe"),
        "center_2x_full_mdd": ("center_2x", "full", "mdd"),
        "center_2x_recent_return": ("center_2x", "recent", "total_return"),
        "center_2x_sealed_return": ("center_2x", "sealed", "total_return"),
        "control_2x_full_annual": ("control_2x", "full", "annual"),
        "control_2x_full_sharpe": ("control_2x", "full", "sharpe"),
        "control_2x_full_mdd": ("control_2x", "full", "mdd"),
        "center_3x_full_annual": ("center_3x", "full", "annual"),
        "center_3x_full_sharpe": ("center_3x", "full", "sharpe"),
        "center_3x_full_mdd": ("center_3x", "full", "mdd"),
    }
    checks = {
        name: math.isclose(
            float(current[portfolio][facet][field]),
            float(expected[portfolio][facet][field]),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        for name, (portfolio, facet, field) in specs.items()
    }
    return {"checks": checks, "all_pass": all(checks.values())}


def build_report() -> dict[str, Any]:
    verified = verify_inputs()
    prereg = verified["preregistration"]
    engine = run_current_pit_engine()
    outputs = engine["outputs"]
    ctx = _context()
    assets = prepare_assets(ctx)
    c_curves = {cost: combine_c(outputs, cost, ctx) for cost in ("2x", "3x", "5x")}
    grid_rows = []
    composed_2x: dict[str, pl.DataFrame] = {}
    for cell in grid_cells():
        fade, hold, stop = cell_params(cell)
        metrics = {}
        exposure = {}
        for cost in ("2x", "3x"):
            bundle = compose_portfolio(
                ctx,
                assets,
                outputs[f"A_{cost}"],
                outputs[f"B_{cost}_{cell}"],
                c_curves[cost],
                cost,
                DEFAULT_PARAMETERS,
            )
            if cost == "2x":
                composed_2x[cell] = bundle["curve"]
            metrics[cost] = curve_metrics(bundle["curve"])
            exposure[cost] = bundle["weight_summary"]
        row = {
            "cell": cell,
            "fade_days": fade,
            "hold_days": hold,
            "stop_loss": stop,
            "metrics": metrics,
            "exposure": exposure,
            "pass_2x": _gate_2x(metrics["2x"], prereg),
            "pass_3x": _gate_3x(metrics["3x"], prereg),
            "annual_2x": metrics["2x"]["full"]["annual"],
            "annual_3x": metrics["3x"]["full"]["annual"],
            "rolling_2x_positive": metrics["2x"]["rolling"]["positive_count"],
            "rolling_3x_positive": metrics["3x"]["rolling"]["positive_count"],
        }
        grid_rows.append(row)

    platform_rows = [row for row in grid_rows if row["cell"] in PLATFORM_CELLS]
    platform = evaluate_platform(platform_rows)
    grid = evaluate_grid(grid_rows, prereg)

    center_2x = next(row for row in grid_rows if row["cell"] == CENTRE_CELL)["metrics"]["2x"]
    center_3x = next(row for row in grid_rows if row["cell"] == CENTRE_CELL)["metrics"]["3x"]
    control_2x = next(row for row in grid_rows if row["cell"] == CONTROL_CELL)["metrics"]["2x"]
    current = {
        "center_2x": center_2x,
        "control_2x": control_2x,
        "center_3x": center_3x,
    }
    reconciliation = _reconcile_r381(current, verified["pit_audit"]["current_portfolio"])

    mc_rows = []
    for sample in sample_parameters(prereg):
        outcome: dict[str, Any] = {"sample": sample["sample"], "parameters": sample["parameters"]}
        for label, cell in (("centre", CENTRE_CELL), ("control", CONTROL_CELL)):
            bundle = compose_portfolio(
                ctx,
                assets,
                outputs["A_2x"],
                outputs[f"B_2x_{cell}"],
                c_curves["2x"],
                "2x",
                sample["parameters"],
            )
            outcome[label] = curve_metrics(bundle["curve"])
            outcome[label]["max_weight_sum"] = bundle["weight_summary"]["max_weight_sum"]
        outcome["sealed_advantage"] = (
            outcome["centre"]["sealed"]["total_return"]
            - outcome["control"]["sealed"]["total_return"]
        )
        outcome["full_annual_advantage"] = (
            outcome["centre"]["full"]["annual"] - outcome["control"]["full"]["annual"]
        )
        mc_rows.append(outcome)
    monte_carlo = evaluate_monte_carlo(mc_rows, prereg)
    centre_sealed = [row["centre"]["sealed"]["total_return"] for row in mc_rows]
    control_sealed = [row["control"]["sealed"]["total_return"] for row in mc_rows]
    advantage = [row["sealed_advantage"] for row in mc_rows]
    monte_carlo["centre_sealed_p05_median_p95"] = [
        _quantile(centre_sealed, 0.05),
        _quantile(centre_sealed, 0.50),
        _quantile(centre_sealed, 0.95),
    ]
    monte_carlo["control_sealed_p05_median_p95"] = [
        _quantile(control_sealed, 0.05),
        _quantile(control_sealed, 0.50),
        _quantile(control_sealed, 0.95),
    ]
    monte_carlo["advantage_p05_median_p95"] = [
        _quantile(advantage, 0.05),
        _quantile(advantage, 0.50),
        _quantile(advantage, 0.95),
    ]
    monte_carlo["advantage_worst"] = min(advantage)
    monte_carlo["advantage_best"] = max(advantage)

    walkforward = run_walkforward(composed_2x)
    ablations = {}
    for mode, label in (
        ("full", "full_candidate"),
        ("without_equity_volatility_target", "without_equity_volatility_target"),
        ("without_defensive_overlay", "without_defensive_overlay"),
        ("without_C_satellite", "without_C_satellite"),
    ):
        bundle = compose_portfolio(
            ctx,
            assets,
            outputs["A_2x"],
            outputs[f"B_2x_{CENTRE_CELL}"],
            c_curves["2x"],
            "2x",
            DEFAULT_PARAMETERS,
            mode=mode,
        )
        ablations[label] = curve_metrics(bundle["curve"])
    full_ablation = ablations["full_candidate"]
    for label, metrics in ablations.items():
        if label == "full_candidate":
            continue
        metrics["delta_vs_full"] = {
            "annual": metrics["full"]["annual"] - full_ablation["full"]["annual"],
            "sharpe": metrics["full"]["sharpe"] - full_ablation["full"]["sharpe"],
            "mdd": metrics["full"]["mdd"] - full_ablation["full"]["mdd"],
            "sealed_return": metrics["sealed"]["total_return"] - full_ablation["sealed"]["total_return"],
        }

    five_x_bundle = compose_portfolio(
        ctx,
        assets,
        outputs["A_5x"],
        outputs[f"B_5x_{CENTRE_CELL}"],
        c_curves["5x"],
        "5x",
        DEFAULT_PARAMETERS,
    )
    five_x_metrics = curve_metrics(five_x_bundle["curve"])
    five_x = {
        "metrics": five_x_metrics,
        "max_weight_sum": five_x_bundle["weight_summary"]["max_weight_sum"],
        **evaluate_five_x(five_x_metrics["full"]),
    }

    baseline_gates = {
        "sealed_return_positive": center_2x["sealed"]["total_return"] > 0.0,
        "sealed_sharpe": center_2x["sealed"]["sharpe"] >= 1.0,
        "sealed_mdd": center_2x["sealed"]["mdd"] >= -0.10,
        "sealed_beats_control_return": (
            center_2x["sealed"]["total_return"] > control_2x["sealed"]["total_return"]
        ),
        "sealed_beats_control_sharpe": (
            center_2x["sealed"]["sharpe"] >= control_2x["sealed"]["sharpe"]
        ),
    }
    all_grid_exposures = [
        row["exposure"][cost]["max_weight_sum"]
        for row in grid_rows
        for cost in ("2x", "3x")
    ]
    monte_carlo_exposures = [
        row[label]["max_weight_sum"]
        for row in mc_rows
        for label in ("centre", "control")
    ]
    no_leverage = exposures_within_limit(
        [*all_grid_exposures, *monte_carlo_exposures]
    )
    all_pass = bool(
        reconciliation["all_pass"]
        and grid["broad_grid_pass"]
        and platform["platform_pass"]
        and monte_carlo["parameter_plateau_pass"]
        and all(baseline_gates.values())
        and no_leverage
    )
    return {
        "round": "R385",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "input_lineage": verified,
        "engine_identity": {
            "engine_sha256": engine["engine_sha256"],
            "membership_sha256": engine["membership_sha256"],
            "extension_frame_sha256": engine["provenance"]["extension_frame_sha256"],
            "variant_sha256": engine["variant_sha256"],
            "r371_runner_sha256": engine["provenance"]["r371_runner_sha256"],
        },
        "r381_reconciliation": reconciliation,
        "current_portfolio": current,
        "broad_grid": grid,
        "platform": platform,
        "platform_rows": platform_rows,
        "grid_rows": grid_rows,
        "monte_carlo": monte_carlo,
        "monte_carlo_rows": mc_rows,
        "walkforward": walkforward,
        "ablations": ablations,
        "five_x_diagnostic": five_x,
        "baseline_gates": baseline_gates,
        "no_leverage": no_leverage,
        "all_pass": all_pass,
        "decision": {
            "status": (
                "candidate_for_paper_trading_review"
                if all_pass
                else "research_only_no_candidate_passed"
            ),
            "selected_cell": CENTRE_CELL if all_pass else None,
            "reason": (
                "current-PIT broad grid, seven-cell platform and paired Monte Carlo all passed"
                if all_pass
                else "at least one preregistered current-PIT gate failed; no threshold change allowed"
            ),
            "five_x_hard_rejection": False,
            "no_leverage": no_leverage,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    centre = report["current_portfolio"]["center_2x"]
    control = report["current_portfolio"]["control_2x"]
    centre_3x = report["current_portfolio"]["center_3x"]
    mc = report["monte_carlo"]
    platform = report["platform"]
    grid = report["broad_grid"]
    five = report["five_x_diagnostic"]
    lines = [
        "# R385 · 当前 PIT 平台与配对蒙特卡洛复核",
        "",
        "R385 不沿用 R374-R379 的旧曲线，27 个 alpha 格全部使用 R381 修复后的名单、行业字段和当前引擎重跑。",
        "",
        "| 当前 PIT 组合 | 年化 | Sharpe | MDD | 近一年 | 封存段 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| hold-30 中心 2× | {centre['full']['annual']:.2%} | {centre['full']['sharpe']:.3f} | {centre['full']['mdd']:.2%} | {centre['recent']['total_return']:.2%} | {centre['sealed']['total_return']:.2%} |",
        f"| 20 日对照 2× | {control['full']['annual']:.2%} | {control['full']['sharpe']:.3f} | {control['full']['mdd']:.2%} | {control['recent']['total_return']:.2%} | {control['sealed']['total_return']:.2%} |",
        f"| hold-30 中心 3× | {centre_3x['full']['annual']:.2%} | {centre_3x['full']['sharpe']:.3f} | {centre_3x['full']['mdd']:.2%} | {centre_3x['recent']['total_return']:.2%} | {centre_3x['sealed']['total_return']:.2%} |",
        "",
        f"- R381 曲线复现：{sum(report['r381_reconciliation']['checks'].values())}/{len(report['r381_reconciliation']['checks'])} 项通过。",
        f"- 27 格 2× 通过：{grid['pass_count_2x']}/{grid['cell_count']}（{grid['pass_rate_2x']:.1%}）；最低边际通过率 {grid['minimum_marginal_pass_rate_2x']:.1%}。",
        f"- 七格 hold-30 平台：{'通过' if platform['platform_pass'] else '未通过'}；最高年化格 {platform['highest_annual_cell']}，最终仍选预登记中心 {CENTRE_CELL}。",
        "",
        "## 配对蒙特卡洛",
        "",
        f"- 中心全窗 v2 门槛通过 {mc['full_pass_count']}/{mc['sample_count']}（{mc['full_pass_rate']:.1%}）。",
        f"- 中心封存门槛通过 {mc['sealed_pass_count']}/{mc['sample_count']}（{mc['sealed_pass_rate']:.1%}）。",
        f"- 同参数封存段中心跑赢对照 {mc['paired_sealed_advantage_count']}/{mc['sample_count']}（{mc['paired_sealed_advantage_rate']:.1%}）。",
        f"- 封存超额 P05/中位/P95：{mc['advantage_p05_median_p95'][0]:+.2%} / {mc['advantage_p05_median_p95'][1]:+.2%} / {mc['advantage_p05_median_p95'][2]:+.2%}；最差 {mc['advantage_worst']:+.2%}。",
        f"- 参数平台判定：{'通过' if mc['parameter_plateau_pass'] else '未通过'}。",
        "",
        "## 成本与诊断",
        "",
        f"- 5× 成本：年化 {five['metrics']['full']['annual']:.2%}，Sharpe {five['metrics']['full']['sharpe']:.3f}，MDD {five['metrics']['full']['mdd']:.2%}；warning={five['warning']}，硬否决=false。",
        f"- 滚动选择：{report['walkforward']['decision']['verdict']}。",
        "- 消融比较：",
        "",
        "| 变体 | 年化 | Sharpe | MDD | 相对完整组合年化 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, metrics in report["ablations"].items():
        delta = metrics.get("delta_vs_full", {}).get("annual", 0.0)
        lines.append(
            f"| {label} | {metrics['full']['annual']:.2%} | {metrics['full']['sharpe']:.3f} | "
            f"{metrics['full']['mdd']:.2%} | {delta:+.2%} |"
        )
    lines.extend([
        "",
        f"**综合判定：{report['decision']['status']}。**",
        "",
        "- 5× 只作为 warning，不参与硬否决。",
        "- 无杠杆，平台格最大总权重不超过 1.0。",
        "- 不生成纸面目标、订单或生产配置。",
        "- 封存窗口不是全新独立样本，历史行业分类也不是完整公告 vintage。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (
        OUTPUT,
        OUTPUT.with_name(OUTPUT.name + ".sha256"),
        RESULT,
        RESULT.with_name(RESULT.name + ".sha256"),
    ):
        if path.exists():
            raise RuntimeError(f"R385 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha256(OUTPUT)}  {OUTPUT.name}\n",
        encoding="ascii",
    )
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(
        f"{sha256(RESULT)}  {RESULT.name}\n",
        encoding="ascii",
    )
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    print(json.dumps(report["platform"], ensure_ascii=False, indent=2))
    print(json.dumps(report["monte_carlo"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
