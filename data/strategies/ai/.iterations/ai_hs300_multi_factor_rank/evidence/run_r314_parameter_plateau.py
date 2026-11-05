from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import shutil
import statistics as st
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
R40 = DATA / "research/r40_engine_data"
MEMBERSHIP = DATA / "research/r78_daily_membership/daily_snapshots.parquet"
PREREG = EVIDENCE / "r314_parameter_plateau_preregistration.json"
OUTPUT = EVIDENCE / "r314_parameter_plateau.json"
A_STRATEGY = "ai_hs300_macro_gate_quality"
B_STRATEGY = "ai_hs300_industry_relative_strength_quality_h20_sl20"
START = date(2021, 6, 1)
END = date(2026, 10, 14)
PHASES = [
    date(2021, 6, 1), date(2022, 1, 4), date(2022, 7, 1),
    date(2023, 1, 3), date(2023, 7, 3), date(2024, 1, 2),
    date(2024, 7, 1), date(2025, 1, 2), date(2025, 7, 1), date(2026, 1, 5),
]
RECENT = [
    date(2025, 10, 14), date(2025, 12, 15), date(2026, 2, 13),
    date(2026, 4, 15), date(2026, 6, 15),
]
RISK_LOOKBACKS = [40, 60, 90]
VOL_TARGETS = [0.12, 0.15, 0.18]
COST = 0.0052

sys.path.insert(0, str(ROOT / "backend"))
from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(obj)
    return obj


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> dict:
    paths = sorted(root.glob("date=*/part.parquet"))
    digest = hashlib.sha256()
    size = 0
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(sha(path).encode())
        digest.update(b"\n")
        size += path.stat().st_size
    return {"files": len(paths), "bytes": size, "sha256": digest.hexdigest()}


def membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP, columns=["query_date", "symbol"])
        .select(pl.col("query_date").cast(pl.Date).alias("effective_date"), pl.col("symbol").cast(pl.String))
        .unique()
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def patched_membership(frame: pl.DataFrame):
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
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def combine_risk(a: pl.DataFrame, b: pl.DataFrame, lookback: int) -> tuple[pl.DataFrame, list[dict]]:
    joined = a.rename({"value": "a"}).join(b.rename({"value": "b"}), on="date", how="inner").sort("date")
    dates = joined["date"].to_list()
    values_a = joined["a"].to_list()
    values_b = joined["b"].to_list()
    returns_a = [values_a[i] / values_a[i - 1] - 1.0 for i in range(1, len(values_a))]
    returns_b = [values_b[i] / values_b[i - 1] - 1.0 for i in range(1, len(values_b))]
    padded_a = [0.0, *returns_a]
    padded_b = [0.0, *returns_b]
    targets: list[tuple[date, float]] = []
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if i < lookback:
                weight = 0.5
            else:
                window_a = padded_a[i - lookback:i]
                window_b = padded_b[i - lookback:i]
                vol_a = max(st.pstdev(window_a), 1e-6)
                vol_b = max(st.pstdev(window_b), 1e-6)
                weight = (1.0 / vol_a) / (1.0 / vol_a + 1.0 / vol_b)
                weight = min(0.8, max(0.2, weight))
            targets.append((day, weight))
            prev_month = month
    target_map = dict(targets)
    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    target = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight_a": target}]
    for i in range(1, len(dates)):
        day = dates[i]
        sleeve_a *= 1.0 + returns_a[i - 1]
        sleeve_b *= 1.0 + returns_b[i - 1]
        portfolio *= 1.0 + target * returns_a[i - 1] + (1.0 - target) * returns_b[i - 1]
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve_a + (1.0 - target) * sleeve_b
            actual_a = target * sleeve_a / total if total else target
            actual_b = 1.0 - actual_a
            next_target = target_map[dates[i + 1]] if i + 1 < len(dates) else target
            turnover = 0.5 * (abs(next_target - actual_a) + abs((1.0 - next_target) - actual_b))
            portfolio *= 1.0 - turnover * COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            target = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weight_records.append({"date": dates[i + 1], "weight_a": target})
    return pl.DataFrame(rows), weight_records


def vol_target(frame: pl.DataFrame, target_vol: float, lookback: int = 60) -> tuple[pl.DataFrame, list[dict]]:
    dates = frame["date"].to_list()
    values = frame["value"].to_list()
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    portfolio = 1.0
    sleeve = 1.0
    target = 0.5
    rows = [{"date": dates[0], "value": portfolio}]
    weights = [{"date": dates[0], "weight": target}]
    for i in range(1, len(dates)):
        day = dates[i]
        sleeve *= 1.0 + returns[i - 1]
        portfolio *= 1.0 + target * returns[i - 1]
        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve + (1.0 - target)
            actual = target * sleeve / total if total else target
            if i + 1 < len(dates):
                if i < lookback:
                    next_target = 0.5
                else:
                    realized = st.pstdev(returns[i - lookback:i]) * math.sqrt(252.0)
                    next_target = 1.0 if realized <= target_vol else min(1.0, max(0.2, target_vol / realized))
            else:
                next_target = target
            turnover = abs(next_target - actual)
            portfolio *= 1.0 - turnover * COST
            sleeve = 1.0
            target = next_target
        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weights.append({"date": dates[i + 1], "weight": target})
    return pl.DataFrame(rows), weights


def main() -> int:
    if OUTPUT.exists() or OUTPUT.with_name(OUTPUT.name + ".sha256").exists():
        raise RuntimeError("R314 result exists; refusing overwrite")
    if sha(PREREG) != PREREG.with_name(PREREG.name + ".sha256").read_text(encoding="ascii").split()[0]:
        raise RuntimeError("R314 preregistration checksum mismatch")

    r112 = module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r112_r314")
    members = membership()
    before_r40 = tree_hash(R40 / "kline_daily_enriched")
    before_prod = tree_hash(DATA / "kline_daily_enriched")
    work = Path(tempfile.mkdtemp(prefix="r314_"))
    try:
        engine_root = work / "engine_data"
        shutil.copytree(R40, engine_root, symlinks=True)
        raw_target = engine_root / "kline_daily"
        if raw_target.is_symlink() or raw_target.is_file():
            raw_target.unlink()
        elif raw_target.exists():
            shutil.rmtree(raw_target)
        shutil.copytree(DATA / "kline_daily", raw_target)
        load_into_registry(DATA)
        engine = StrategyEngine(
            strategy_dirs=[DATA / "strategies/ai"],
            override_loader=lambda sid: strategy_config.load_override(DATA, sid),
            industry_loader=industry_loader_for(DATA),
        )
        service = StrategyBacktestService(BacktestEngine(KlineRepository(DataStore(engine_root))), engine)

        def run(strategy_id: str, start: date, cost_factor: int) -> pl.DataFrame:
            cfg = StrategyBacktestConfig(
                strategy_id=strategy_id, symbols=None, start=start, end=END,
                params={}, overrides={}, matching="open_t+1",
                fees_pct=0.0002 * cost_factor,
                commission_pct=0.0002 * cost_factor,
                stamp_tax_pct=0.0005 if cost_factor == 2 else 0.001,
                slippage_bps=5.0 * cost_factor,
                max_positions=10, max_exposure_pct=1.0, position_sizing="equal",
                mode="position", asset_type="stock", hs300=True,
            )
            with patched_membership(members):
                result = service.run(cfg)
            if result.error:
                raise RuntimeError(f"{strategy_id} {start} {cost_factor}x: {result.error}")
            return curve_frame(result.equity_curve)

        curves: dict[str, dict[str, tuple[pl.DataFrame, pl.DataFrame]]] = {"phases": {}, "recent": {}}
        for bucket, starts in (("phases", PHASES), ("recent", RECENT)):
            for start in starts:
                curves[bucket][start.isoformat()] = (run(A_STRATEGY, start, 2), run(B_STRATEGY, start, 2))
                print(f"loaded {bucket} {start}", flush=True)
        stress_a = run(A_STRATEGY, START, 3)
        stress_b = run(B_STRATEGY, START, 3)

        # Verify the generic center implementation is curve-identical to R112.
        center_risk, _ = combine_risk(*curves["phases"][START.isoformat()], 60)
        reference_risk, _ = r112._combine(*curves["phases"][START.isoformat()], "risk")
        parity_join = center_risk.rename({"value": "generic"}).join(
            reference_risk.rename({"value": "reference"}), on="date", how="inner"
        )
        center_parity_max = float(
            (parity_join["generic"] - parity_join["reference"]).abs().max() or 0.0
        )

        grid: dict[str, dict] = {}
        weight_bounds_ok = True
        for risk_lookback in RISK_LOOKBACKS:
            for target_vol in VOL_TARGETS:
                key = f"rp{risk_lookback}_vol{int(target_vol * 100)}"
                phase_stats = {}
                overlay_weights = []
                for day_key, (a, b) in curves["phases"].items():
                    risk, rp_weights = combine_risk(a, b, risk_lookback)
                    overlay, weights = vol_target(risk, target_vol)
                    overlay_weights.extend(float(row["weight"]) for row in weights)
                    phase_stats[day_key] = r112._stats(overlay)
                recent_stats = {}
                for day_key, (a, b) in curves["recent"].items():
                    risk, _ = combine_risk(a, b, risk_lookback)
                    overlay, weights = vol_target(risk, target_vol)
                    overlay_weights.extend(float(row["weight"]) for row in weights)
                    recent_stats[day_key] = r112._stats(overlay)
                risk, _ = combine_risk(stress_a, stress_b, risk_lookback)
                stress_overlay, stress_weights = vol_target(risk, target_vol)
                overlay_weights.extend(float(row["weight"]) for row in stress_weights)
                weight_bounds_ok = weight_bounds_ok and min(overlay_weights) >= 0.0 and max(overlay_weights) <= 1.0
                full = phase_stats[START.isoformat()]
                long_agg = r112._aggregate([phase_stats[k] for k in sorted(phase_stats)])
                recent_agg = r112._aggregate([recent_stats[k] for k in sorted(recent_stats)])
                grid[key] = {
                    "risk_parity_lookback": risk_lookback,
                    "target_vol": target_vol,
                    "full": full,
                    "long_phase_mean": {
                        k: long_agg[k]
                        for k in ("total_return_mean", "annual_mean", "sharpe_mean", "mdd_mean", "mdd_worst", "positive_phases")
                    },
                    "recent": {
                        k: recent_agg[k]
                        for k in ("total_return_mean", "annual_mean", "sharpe_mean", "mdd_mean", "mdd_worst", "positive_phases")
                    },
                    "stress_3x": r112._stats(stress_overlay),
                }
                print(
                    f"{key}: full_ret={full['total_return']:.3f} mdd={full['mdd']:.3f} "
                    f"sharpe={full['sharpe']:.3f} recent_mean={recent_agg['total_return_mean']:.3f}",
                    flush=True,
                )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    after_r40 = tree_hash(R40 / "kline_daily_enriched")
    after_prod = tree_hash(DATA / "kline_daily_enriched")
    center = grid["rp60_vol15"]
    drawdown_pass = sum(x["full"]["mdd"] >= -0.25 for x in grid.values())
    return_pass = sum(x["full"]["annual"] >= 0.05 and x["full"]["sharpe"] >= 0.55 for x in grid.values())
    recent_pass = sum(x["recent"]["total_return_mean"] > 0 and x["recent"]["positive_phases"] >= 4 for x in grid.values())
    stress_pass = sum(x["stress_3x"]["total_return"] > 0 and x["stress_3x"]["mdd"] >= -0.30 for x in grid.values())
    gates = {
        "P1_execution": (
            len(curves["phases"]) == 10 and len(curves["recent"]) == 5 and len(grid) == 9
            and center_parity_max <= 1e-9
        ),
        "P2_center_full": (
            center["full"]["mdd"] >= -0.25
            and center["full"]["annual"] >= 0.05
            and center["full"]["sharpe"] >= 0.60
        ),
        "P3_drawdown_plateau": drawdown_pass >= 7,
        "P4_return_plateau": return_pass >= 7,
        "P5_recent_plateau": recent_pass >= 7,
        "P6_stress_plateau": stress_pass >= 7,
        "P7_no_leverage": weight_bounds_ok,
    }
    payload = {
        "round": "R314",
        "classification": "preregistered parameter-neighborhood robustness check; no best-cell selection",
        "preregistration_sha256": sha(PREREG),
        "runner_sha256": sha(Path(__file__).resolve()),
        "grid": {"risk_parity_lookbacks": RISK_LOOKBACKS, "target_vols": VOL_TARGETS, "rebalance": "monthly", "round_trip_cost": COST},
        "center": "rp60_vol15",
        "center_generic_parity_max_abs_diff": round(center_parity_max, 12),
        "pass_counts": {
            "drawdown": drawdown_pass,
            "return": return_pass,
            "recent": recent_pass,
            "stress": stress_pass,
        },
        "cells": grid,
        "gates": gates,
        "selected": all(gates.values()),
        "input_isolation": {
            "r40_before": before_r40, "r40_after": after_r40,
            "production_before": before_prod, "production_after": after_prod,
            "stable": before_r40 == after_r40 and before_prod == after_prod,
        },
        "decision": {
            "status": "parameter_robust_center" if all(gates.values()) else "parameter_fragile_center",
            "best_cell_selected": False,
            "production_write": False, "provider_call": False,
        },
        "limits": [
            "Neighborhood cells share overlapping history and are not independent samples.",
            "Center remains the only candidate; no grid winner is selected after the fact.",
            "R40 is a local research snapshot and does not resolve canonical data drift.",
        ],
    }
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode()
    with OUTPUT.open("xb") as stream:
        stream.write(raw)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {OUTPUT.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "center": {k: center["full"][k] for k in ("total_return", "annual", "sharpe", "mdd")},
        "pass_counts": payload["pass_counts"],
        "gates": gates,
        "selected": payload["selected"],
        "output": str(OUTPUT.relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
