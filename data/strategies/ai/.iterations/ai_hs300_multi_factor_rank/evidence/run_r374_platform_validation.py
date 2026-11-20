"""R374: validate the hold-30 platform with full 3x costs and rolling years."""
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
PREREG = EVIDENCE / "r374_preregistration.json"
R371 = EVIDENCE / "r371_alpha_parameter_grid.json"
R373 = EVIDENCE / "r373_hold30_recheck.json"
R360 = EVIDENCE / "r360_industry_fade_backtest.json"
OUTPUT = EVIDENCE / "r374_platform_validation.json"
RESULT = EVIDENCE / "r374_result.md"
R371_CACHE = RESEARCH / "r371_cache"
R374_CACHE = RESEARCH / "r374_cache"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
ROLLING_STARTS = (date(2022, 5, 5), date(2023, 5, 5), date(2024, 5, 5), date(2025, 5, 5))
ROLLING_LENGTH = 252
ROLLING_POSITIVE_MIN = 3
ROLLING_WINDOW_COUNT = 4
PLATFORM_CENTER_CELL = "fade05_h30_sl20"
R360_CONTROL_CELL = "fade05_h20_sl20"
COST_HARD_GATE = "3x"
FIVE_X_HARD_REJECTION = False
PLATFORM_THRESHOLD = {
    "annual_2x_min": 0.08,
    "sharpe_2x_min": 1.0,
    "mdd_2x_min": -0.20,
    "annual_3x_min": 0.05,
    "sharpe_3x_min": 0.80,
    "mdd_3x_min": -0.25,
}
PLATFORM_CELLS = (
    "fade03_h30_sl15",
    "fade05_h30_sl15",
    "fade05_h30_sl20",
    "fade05_h30_sl25",
    "fade10_h30_sl15",
    "fade10_h30_sl20",
    "fade10_h30_sl25",
)
COST_SCENARIOS = {
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


def _curve(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")


def _curve_frame(curve: list[dict[str, Any]]) -> pl.DataFrame:
    rows = []
    for item in curve:
        raw = item.get("date")
        day = raw if isinstance(raw, date) else date.fromisoformat(str(raw)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def stats(curve: pl.DataFrame) -> dict[str, float | int]:
    values = [float(value) for value in curve["value"].to_list()]
    if len(values) < 2:
        raise ValueError("curve too short")
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


def year_returns(curve: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in curve.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous_end: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        start_value = previous_end if previous_end is not None else values[0]
        output[str(year)] = values[-1] / start_value - 1.0
        previous_end = values[-1]
    return output


def segment_return(curve: pl.DataFrame, start: date, end: date) -> float | None:
    part = curve.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 2:
        return None
    return float(part["value"][-1] / part["value"][0] - 1.0)


def rolling_year_windows(curve: pl.DataFrame | list[dict[str, Any]], *, length: int = ROLLING_LENGTH) -> list[dict[str, Any]]:
    if isinstance(curve, pl.DataFrame):
        dates = curve["date"].to_list()
        values = curve["value"].to_list()
    else:
        dates = [date.fromisoformat(str(row["date"])[:10]) for row in curve]
        values = [float(row["value"]) for row in curve]
    windows = []
    for start_index in range(0, len(values) - length):
        end_index = start_index + length
        windows.append({
            "start": str(dates[start_index]),
            "end": str(dates[end_index]),
            "n_returns": end_index - start_index,
            "return": values[end_index] / values[start_index] - 1.0,
        })
    return windows


def rolling_year_summary(curve: pl.DataFrame, starts: tuple[date, ...] = ROLLING_STARTS) -> dict[str, Any]:
    windows = []
    for start in starts:
        part = curve.filter(pl.col("date") >= start).sort("date")
        if part.height < 2:
            raise ValueError(f"rolling window has no data from {start}")
        sample = part.head(ROLLING_LENGTH + 1)
        if sample.height != ROLLING_LENGTH + 1:
            raise ValueError(f"rolling window from {start} does not contain {ROLLING_LENGTH} returns")
        windows.append({
            "start": str(start),
            "actual_end": str(sample["date"][-1]),
            "n_returns": sample.height - 1,
            "return": float(sample["value"][-1] / sample["value"][0] - 1.0),
        })
    return {
        "window_count": len(windows),
        "positive_count": sum(window["return"] > 0 for window in windows),
        "positive_rate": sum(window["return"] > 0 for window in windows) / len(windows),
        "windows": windows,
    }


def cell_slug(fade: int, hold: int, stop: float) -> str:
    return f"fade{fade:02d}_h{hold:02d}_sl{round(abs(stop) * 100):02d}"


def cell_params(cell: str) -> tuple[int, int, float]:
    parts = cell.split("_")
    return int(parts[0][4:]), int(parts[1][1:]), -int(parts[2][2:]) / 100.0


def _gate(metrics: dict[str, Any], yearly: dict[str, float], recent: float | None, cost: str) -> dict[str, Any]:
    if cost == "2x":
        checks = {
            "annual": metrics["annual"] >= PLATFORM_THRESHOLD["annual_2x_min"],
            "sharpe": metrics["sharpe"] >= PLATFORM_THRESHOLD["sharpe_2x_min"],
            "mdd": metrics["mdd"] >= PLATFORM_THRESHOLD["mdd_2x_min"],
            "year_2022": yearly.get("2022", -1.0) >= 0.0,
            "year_2023": yearly.get("2023", -1.0) >= 0.0,
            "recent": recent is not None and recent > 0.0,
        }
    elif cost == "3x":
        checks = {
            "annual": metrics["annual"] >= PLATFORM_THRESHOLD["annual_3x_min"],
            "sharpe": metrics["sharpe"] >= PLATFORM_THRESHOLD["sharpe_3x_min"],
            "mdd": metrics["mdd"] >= PLATFORM_THRESHOLD["mdd_3x_min"],
        }
    else:
        raise ValueError(cost)
    return {"pass": all(checks.values()), "checks": checks}


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r374_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r374_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r374_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r374_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r374_r114"),
        "r371": module(EVIDENCE / "run_r371_alpha_parameter_grid.py", "r374_r371"),
    }


def _baseline_curves(ctx: dict[str, Any], cost: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    r359 = ctx["r359"]
    a = _curve(RESEARCH / f"r344_cache/ai_hs300_macro_gate_quality_2022-05-05_{cost}_p10.parquet")
    starts = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
    cohort_curves = [
        _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_{cost}_p50.parquet")
        for start in starts
    ]
    return a, r359.combine_cohort_curves(cohort_curves, starts)


def _final_curve(a: pl.DataFrame, b: pl.DataFrame, c: pl.DataFrame, ctx: dict[str, Any], cost: str) -> pl.DataFrame:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    scenario = COST_SCENARIOS[cost]
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_112 = r112.ROUND_TRIP_COST
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    try:
        r112.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r114.TARGET_VOL = 0.15
        r114.ROUND_TRIP_COST = scenario["portfolio_turnover"]
        r331.LOOKBACK = 60
        r331.TARGET_VOL = 0.15
        r331.EQUITY_WEIGHT_MAX = 1.0 / 3.0
        risk, _ = r344._combine_three((a, b, c), scenario["portfolio_turnover"], 0.10)
        equity = r114._vol_target(risk)["curve"]
        gold = r331.asset_frame("gold", "518880.SH")
        treasury = r331.asset_frame("treasury", "511260.SH")
        dividend = r331.asset_frame("dividend", "510880.SH")
        gold_proxy = r344.gold_trend_proxy(gold, treasury, dividend, scenario["defensive_turnover"])
        return r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            treasury,
            dividend,
            round_trip_cost=scenario["defensive_turnover"],
        )["curve"]
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def _run_cost_cell(
    ctx: dict[str, Any],
    cell: str,
    *,
    fade: int,
    hold: int,
    stop: float,
    cost: str,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    r371 = ctx["r371"]
    from app.backtest.engine import BacktestEngine
    from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
    from app.config import settings
    from app.factors.store import load_into_registry
    from app.indicators import pipeline
    from app.services.industry_map import industry_loader_for
    from app.strategy.engine import StrategyEngine
    from app.tickflow.repository import DataStore, KlineRepository

    if cost not in ("3x", "5x"):
        raise ValueError(f"R374 only materializes stressed B-sleeve curves, got {cost}")
    params = COST_SCENARIOS[cost]["stock"]
    r371._write_variant_definitions()
    strategy_id = f"r371_industry_fade_{fade}d_quality_h20_sl20"
    strategy_path = r371.VARIANTS / "strategies" / f"{strategy_id}.py"
    strategy_hash = sha256(strategy_path)
    membership = r371._membership()
    membership_hash = hashlib.sha256(membership.write_csv().encode("utf-8")).hexdigest()
    expected_meta = {
        "strategy_id": strategy_id,
        "fade_days": fade,
        "hold_days": hold,
        "stop_loss": stop,
        "start": str(START),
        "end": str(END),
        "cost": cost,
        "fees_pct": params["fees"],
        "commission_pct": params["commission"],
        "stamp_tax_pct": params["stamp"],
        "slippage_bps": params["slippage"],
        "matching": "open_t+1",
        "max_positions": 10,
        "membership_sha256": membership_hash,
        "strategy_sha256": strategy_hash,
    }
    curve_path = R374_CACHE / f"{cell}_{cost}.parquet"
    meta_path = R374_CACHE / f"{cell}_{cost}.meta.json"
    if curve_path.exists() or meta_path.exists():
        if not curve_path.exists() or not meta_path.exists():
            raise RuntimeError(f"R374 incomplete cache entry: {cell} {cost}")
        if json.loads(meta_path.read_text(encoding="utf-8")) != expected_meta:
            raise RuntimeError(f"R374 cache metadata mismatch: {cell} {cost}")
        return _curve(curve_path), expected_meta

    work = Path(tempfile.mkdtemp(prefix="r374_engine_data_"))
    old_data = settings.data_dir
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        module(EVIDENCE / "r360_isolation.py", "r374_isolation").prepare_isolated_data_root(engine_root, DATA)
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
            overrides={"max_hold_days": hold, "stop_loss": stop},
            matching="open_t+1",
            fees_pct=params["fees"],
            commission_pct=params["commission"],
            stamp_tax_pct=params["stamp"],
            slippage_bps=params["slippage"],
            holding_days=hold,
            max_positions=10,
            max_exposure_pct=1.0,
            initial_capital=1_000_000.0,
            position_sizing="equal",
            mode="position",
            asset_type="stock",
            hs300=True,
        )
        started = time.perf_counter()
        with r371._patched_membership(membership):
            result = service.run(config)
        if result.error:
            raise RuntimeError(f"R374 {cell} {cost}: {result.error}")
        curve = _curve_frame(result.equity_curve)
        if curve["date"].min() != START or curve["date"].max() != END:
            raise RuntimeError(f"R374 date coverage mismatch: {cell} {cost}")
        R374_CACHE.mkdir(parents=True, exist_ok=True)
        curve.write_parquet(curve_path, compression="zstd")
        meta_path.write_text(json.dumps(expected_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"R374 ran {cell} {cost} in {time.perf_counter() - started:.1f}s: {stats(curve)}", flush=True)
        return curve, expected_meta
    finally:
        settings.data_dir = old_data
        shutil.rmtree(work, ignore_errors=True)


def _evaluate_cell(
    ctx: dict[str, Any],
    cell: str,
    *,
    a2: pl.DataFrame,
    c2: pl.DataFrame,
    a3: pl.DataFrame,
    c3: pl.DataFrame,
) -> dict[str, Any]:
    b2 = _curve(R371_CACHE / f"{cell}.parquet")
    final2 = _final_curve(a2, b2, c2, ctx, "2x")
    metrics2 = stats(final2)
    yearly2 = year_returns(final2)
    recent2 = segment_return(final2, RECENT_START, END)
    gate2 = _gate(metrics2, yearly2, recent2, "2x")
    rolling2 = rolling_year_summary(final2)

    fade, hold, stop = cell_params(cell)
    b3, meta3 = _run_cost_cell(ctx, cell, fade=fade, hold=hold, stop=stop, cost="3x")
    final3 = _final_curve(a3, b3, c3, ctx, "3x")
    metrics3 = stats(final3)
    yearly3 = year_returns(final3)
    recent3 = segment_return(final3, RECENT_START, END)
    gate3 = _gate(metrics3, yearly3, recent3, "3x")
    rolling3 = rolling_year_summary(final3)
    return {
        "cell": cell,
        "annual_2x": metrics2["annual"],
        "sharpe_2x": metrics2["sharpe"],
        "mdd_2x": metrics2["mdd"],
        "recent_2x": recent2,
        "annual_3x": metrics3["annual"],
        "sharpe_3x": metrics3["sharpe"],
        "mdd_3x": metrics3["mdd"],
        "recent_3x": recent3,
        "yearly_2x": yearly2,
        "yearly_3x": yearly3,
        "joint_2x": gate2["pass"],
        "joint_3x": gate3["pass"],
        "checks_2x": gate2["checks"],
        "checks_3x": gate3["checks"],
        "rolling_2x_positive": rolling2["positive_count"],
        "rolling_3x_positive": rolling3["positive_count"],
        "rolling_2x": rolling2,
        "rolling_3x": rolling3,
        "input_meta_3x": meta3,
    }


def summarise_platform(rows: list[dict[str, Any]], threshold: dict[str, float] | None = None) -> dict[str, Any]:
    del threshold
    if not rows:
        return {"cell_count": 0, "pass_count": 0, "platform_pass": False, "failed_cells": []}
    passed = [
        row
        for row in rows
        if row["joint_2x"]
        and row["joint_3x"]
        and row["rolling_2x_positive"] >= ROLLING_POSITIVE_MIN
        and row["rolling_3x_positive"] >= ROLLING_POSITIVE_MIN
    ]
    failed = [row["cell"] for row in rows if row not in passed]
    return {
        "cell_count": len(rows),
        "pass_count": len(passed),
        "pass_rate": len(passed) / len(rows),
        "platform_pass": len(passed) == len(rows) and len(rows) == len(PLATFORM_CELLS),
        "failed_cells": failed,
        "annual_2x_range": [min(row["annual_2x"] for row in rows), max(row["annual_2x"] for row in rows)],
        "annual_3x_range": [min(row["annual_3x"] for row in rows), max(row["annual_3x"] for row in rows)],
    }


def select_platform_centre(rows: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    if not summary["platform_pass"]:
        return {"selected_cell": None, "reason": "platform failed; no centre may be selected"}
    by_cell = {row["cell"]: row for row in rows}
    centre = by_cell[PLATFORM_CENTER_CELL]
    highest = max(rows, key=lambda row: row["annual_2x"])["cell"]
    if not (
        centre["joint_2x"]
        and centre["joint_3x"]
        and centre["rolling_2x_positive"] >= ROLLING_POSITIVE_MIN
        and centre["rolling_3x_positive"] >= ROLLING_POSITIVE_MIN
    ):
        return {"selected_cell": None, "reason": "pre-registered centre failed despite a platform summary"}
    return {
        "selected_cell": PLATFORM_CENTER_CELL,
        "reason": "pre-registered centre inside a fully passing contiguous region; not the highest-annual cell",
        "highest_annual_cell_not_selected": highest,
        "centre_annual_2x": centre["annual_2x"],
        "highest_annual_2x": max(row["annual_2x"] for row in rows),
    }


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if tuple(prereg["platform_cells"]) != PLATFORM_CELLS:
        raise ValueError("R374 platform cells do not match preregistration")
    ctx = _context()
    a2, c2 = _baseline_curves(ctx, "2x")
    a3, c3 = _baseline_curves(ctx, "3x")
    rows = [_evaluate_cell(ctx, cell, a2=a2, c2=c2, a3=a3, c3=c3) for cell in PLATFORM_CELLS]
    control = _evaluate_cell(ctx, R360_CONTROL_CELL, a2=a2, c2=c2, a3=a3, c3=c3)
    summary = summarise_platform(rows, PLATFORM_THRESHOLD)
    decision = select_platform_centre(rows, summary)
    selected = next((row for row in rows if row["cell"] == decision["selected_cell"]), None)
    if selected is not None and selected["cell"] == PLATFORM_CENTER_CELL:
        a5, c5 = _baseline_curves(ctx, "5x")
        fade, hold, stop = cell_params(PLATFORM_CENTER_CELL)
        b5, _ = _run_cost_cell(ctx, PLATFORM_CENTER_CELL, fade=fade, hold=hold, stop=stop, cost="5x")
        metrics5 = stats(_final_curve(a5, b5, c5, ctx, "5x"))
        five_x = {"cell": PLATFORM_CENTER_CELL, "metrics": metrics5, "hard_rejection": FIVE_X_HARD_REJECTION}
    else:
        five_x = {"cell": None, "metrics": None, "hard_rejection": FIVE_X_HARD_REJECTION}
    center_2x = control
    frozen = json.loads(R360.read_text(encoding="utf-8"))["performance"]["candidate"]["2x"]
    center_matches = all(
        math.isclose(float(center_2x[key]), float(frozen[frozen_key]), rel_tol=0.0, abs_tol=1e-9)
        for key, frozen_key in (("annual_2x", "annual"), ("sharpe_2x", "sharpe"), ("mdd_2x", "mdd"))
    )
    if not center_matches:
        raise RuntimeError("R374 2x centre does not reconstruct the frozen R360 result")
    return {
        "round": "R374",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "source_r371_sha256": sha256(R371),
        "source_r373_sha256": sha256(R373),
        "cost_hard_gate": COST_HARD_GATE,
        "five_x_hard_rejection": FIVE_X_HARD_REJECTION,
        "cost_scenarios": COST_SCENARIOS,
        "center_2x_reconstructs_r360": center_matches,
        "control_cell": R360_CONTROL_CELL,
        "platform_center_cell": PLATFORM_CENTER_CELL,
        "control": control,
        "platform": summary,
        "decision": decision,
        "five_x_diagnostic": five_x,
        "rows": rows,
        "known_limits": prereg["known_limits"],
        "decision_flags": {
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["platform"]
    lines = [
        "# R374 · hold-30 平台全成本 3× 与滚动一年复核",
        "",
        f"平台门槛：{summary['platform_pass']}；通过 {summary['pass_count']}/{summary['cell_count']} 格。",
        "",
        "| 格 | 2×年化 | 2×Sharpe | 2×MDD | 2×滚动正年 | 3×年化 | 3×Sharpe | 3×MDD | 3×滚动正年 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in report["rows"]:
        lines.append(
            f"| {row['cell']} | {row['annual_2x']:.2%} | {row['sharpe_2x']:.3f} | {row['mdd_2x']:.2%} | "
            f"{row['rolling_2x_positive']}/4 | {row['annual_3x']:.2%} | {row['sharpe_3x']:.3f} | "
            f"{row['mdd_3x']:.2%} | {row['rolling_3x_positive']}/4 |"
        )
    if report["five_x_diagnostic"]["metrics"] is not None:
        metrics = report["five_x_diagnostic"]["metrics"]
        lines.append(
            f"- 5× 中心诊断：年化 {metrics['annual']:.2%}，Sharpe {metrics['sharpe']:.3f}，MDD {metrics['mdd']:.2%}。"
        )
    lines.extend([
        "",
        f"- 中心选择：{report['decision'].get('selected_cell')}；{report['decision'].get('reason')}",
        f"- 最高年化格：{report['decision'].get('highest_annual_cell_not_selected')}，未被选中。",
        "- 3× 对股票、组合换手和防御层使用同一成本倍数；5× 只做中心点诊断。",
        "- 平台继承 R371/R373 的事后参数网格，不是独立样本外发现。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ])
    markdown_text = "\n".join(lines)
    return markdown_text.replace("\n- 5×", "\n\n- 5×")


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R374 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
