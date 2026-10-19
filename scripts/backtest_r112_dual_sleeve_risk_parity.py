#!/usr/bin/env python3
"""R112: dual-sleeve risk parity on historical HS300 membership.

The two sleeves are:

- A: ai_hs300_macro_gate_quality
- B: ai_hs300_industry_relative_strength_quality_h20_sl20

The test compares A, B, fixed 50/50 and 60-day inverse-volatility risk parity.
Every sleeve is run under the exact daily historical HS300 membership, T+1
open execution and 3x cost.  The portfolio layer rebalances monthly and pays
an extra 0.52% round-trip cost on turnover.
"""
from __future__ import annotations

import json
import math
import statistics as st
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest import strategy as strategy_module
from app.backtest.engine import BacktestEngine
from app.backtest.strategy import StrategyBacktestConfig, StrategyBacktestService
from app.factors.store import load_into_registry
from app.services.industry_map import industry_loader_for
from app.strategy import config as strategy_config
from app.strategy.engine import StrategyEngine
from app.tickflow.repository import DataStore, KlineRepository

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EVIDENCE = (
    DATA / "strategies" / "ai" / ".iterations" / "ai_hs300_multi_factor_rank" / "evidence"
)
OUT = EVIDENCE / "r112_dual_sleeve_risk_parity.json"
CACHE = DATA / "research" / "r112_cache"
MEMBERSHIP = DATA / "research" / "r78_daily_membership" / "daily_snapshots.parquet"
START = date(2021, 6, 1)
END = date(2026, 10, 16)
PHASE_STARTS = [
    date(2021, 6, 1),
    date(2022, 1, 4),
    date(2022, 7, 1),
    date(2023, 1, 3),
    date(2023, 7, 3),
    date(2024, 1, 2),
    date(2024, 7, 1),
    date(2025, 1, 2),
    date(2025, 7, 1),
    date(2026, 1, 5),
]
RECENT_STARTS = [
    date(2025, 10, 14),
    date(2025, 12, 15),
    date(2026, 2, 13),
    date(2026, 4, 15),
    date(2026, 6, 15),
]
SLEEVES = {
    "A_macro_gate_quality": "ai_hs300_macro_gate_quality",
    "B_industry_relative_strength_h20_sl20": "ai_hs300_industry_relative_strength_quality_h20_sl20",
}
ROUND_TRIP_COST = 0.0052


def _historical_membership() -> pl.DataFrame:
    return (
        pl.read_parquet(MEMBERSHIP)
        .select([
            pl.col("query_date").cast(pl.Date).alias("effective_date"),
            pl.col("symbol").cast(pl.String),
        ])
        .unique(subset=["effective_date", "symbol"], keep="last")
        .sort(["effective_date", "symbol"])
    )


@contextmanager
def _patched_membership(membership: pl.DataFrame):
    original = strategy_module._hs300_membership_frame
    strategy_module._hs300_membership_frame = lambda _start, _end: membership
    try:
        yield
    finally:
        strategy_module._hs300_membership_frame = original


def _service() -> StrategyBacktestService:
    load_into_registry(DATA)
    engine = StrategyEngine(
        strategy_dirs=[DATA / "strategies" / "ai"],
        override_loader=lambda sid: strategy_config.load_override(DATA, sid),
        industry_loader=industry_loader_for(DATA),
    )
    return StrategyBacktestService(
        BacktestEngine(KlineRepository(DataStore(DATA))),
        engine,
    )


def _cache_path(strategy_id: str, start: date, end: date) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{strategy_id}_{start.isoformat()}_{end.isoformat()}.parquet"


def _curve_frame(curve: list[dict]) -> pl.DataFrame:
    rows = []
    for item in curve or []:
        raw_date = item.get("date")
        if isinstance(raw_date, date):
            day = raw_date
        else:
            day = date.fromisoformat(str(raw_date)[:10])
        rows.append({"date": day, "value": float(item.get("value", 0.0))})
    if not rows:
        raise RuntimeError("empty equity curve")
    return pl.DataFrame(rows).sort("date").unique(subset=["date"], keep="last")


def _run_sleeve(
    service: StrategyBacktestService,
    membership: pl.DataFrame,
    *,
    strategy_id: str,
    start: date,
    end: date,
) -> pl.DataFrame:
    path = _cache_path(strategy_id, start, end)
    if path.exists():
        return pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))

    cfg = StrategyBacktestConfig(
        strategy_id=strategy_id,
        symbols=None,
        start=start,
        end=end,
        params={},
        overrides={},
        matching="open_t+1",
        fees_pct=0.0006,
        commission_pct=0.0006,
        stamp_tax_pct=0.001,
        slippage_bps=15.0,
        max_positions=10,
        position_sizing="equal",
        mode="position",
        asset_type="stock",
        hs300=True,
    )
    started = time.perf_counter()
    with _patched_membership(membership):
        result = service.run(cfg)
    if result.error:
        raise RuntimeError(f"{strategy_id} {start}: {result.error}")
    frame = _curve_frame(result.equity_curve)
    frame.write_parquet(path)
    print(
        f"{strategy_id} {start}: days={frame.height} "
        f"ret={result.stats.get('total_return')} "
        f"elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return frame


def _stats(curve: pl.DataFrame) -> dict:
    values = curve["value"].to_list()
    if len(values) < 2:
        raise RuntimeError("curve too short")
    returns = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
    total = values[-1] / values[0] - 1.0
    annual = (1.0 + total) ** (252.0 / len(returns)) - 1.0
    sd = st.pstdev(returns)
    sharpe = st.mean(returns) / sd * math.sqrt(252.0) if sd > 0 else 0.0
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return {
        "total_return": round(total, 6),
        "annual": round(annual, 6),
        "sharpe": round(sharpe, 4),
        "mdd": round(mdd, 6),
        "days": len(values),
    }


def _monthly_targets(dates: list[date], returns_a: list[float], returns_b: list[float], mode: str):
    targets = []
    weight = 0.5
    history_a: list[float] = []
    history_b: list[float] = []
    prev_month = None
    for i, day in enumerate(dates):
        month = (day.year, day.month)
        if i == 0 or month != prev_month:
            if mode == "fixed":
                weight = 0.5
            elif i < 60:
                weight = 0.5
            else:
                window_a = returns_a[i - 60:i]
                window_b = returns_b[i - 60:i]
                vol_a = max(st.pstdev(window_a), 1e-6)
                vol_b = max(st.pstdev(window_b), 1e-6)
                weight = (1.0 / vol_a) / (1.0 / vol_a + 1.0 / vol_b)
                weight = min(0.8, max(0.2, weight))
            targets.append((day, weight))
            prev_month = month
        if i > 0:
            history_a.append(returns_a[i])
            history_b.append(returns_b[i])
    return targets


def _combine(a: pl.DataFrame, b: pl.DataFrame, mode: str) -> tuple[pl.DataFrame, list[dict]]:
    joined = (
        a.rename({"value": "a"})
        .join(b.rename({"value": "b"}), on="date", how="inner")
        .sort("date")
    )
    dates = joined["date"].to_list()
    values_a = joined["a"].to_list()
    values_b = joined["b"].to_list()
    returns_a = [values_a[i] / values_a[i - 1] - 1.0 for i in range(1, len(values_a))]
    returns_b = [values_b[i] / values_b[i - 1] - 1.0 for i in range(1, len(values_b))]
    # returns[i] is the return from dates[i] to dates[i+1].
    targets = _monthly_targets(dates, [0.0, *returns_a], [0.0, *returns_b], mode)
    target_map = {day: weight for day, weight in targets}

    portfolio = 1.0
    sleeve_a = 1.0
    sleeve_b = 1.0
    target = target_map[dates[0]]
    rows = [{"date": dates[0], "value": portfolio}]
    weight_records = [{"date": dates[0], "weight_a": target}]

    for i in range(1, len(dates)):
        day = dates[i]
        ret_a = returns_a[i - 1]
        ret_b = returns_b[i - 1]
        sleeve_a *= 1.0 + ret_a
        sleeve_b *= 1.0 + ret_b
        portfolio *= 1.0 + target * ret_a + (1.0 - target) * ret_b

        next_month = i + 1 == len(dates) or (dates[i + 1].year, dates[i + 1].month) != (day.year, day.month)
        if next_month:
            total = target * sleeve_a + (1.0 - target) * sleeve_b
            actual_a = target * sleeve_a / total if total else target
            actual_b = 1.0 - actual_a
            if i + 1 < len(dates):
                next_target = target_map[dates[i + 1]]
            else:
                next_target = target
            turnover = 0.5 * (abs(next_target - actual_a) + abs((1.0 - next_target) - actual_b))
            portfolio *= 1.0 - turnover * ROUND_TRIP_COST
            sleeve_a = 1.0
            sleeve_b = 1.0
            target = next_target

        rows.append({"date": day, "value": portfolio})
        if next_month and i + 1 < len(dates):
            weight_records.append({"date": dates[i + 1], "weight_a": target})

    return pl.DataFrame(rows), weight_records


def _aggregate(records: list[dict]) -> dict:
    def values(key: str):
        return [record[key] for record in records]

    return {
        "total_return_mean": round(st.mean(values("total_return")), 6),
        "total_return_min": round(min(values("total_return")), 6),
        "total_return_max": round(max(values("total_return")), 6),
        "positive_phases": sum(value > 0 for value in values("total_return")),
        "annual_mean": round(st.mean(values("annual")), 6),
        "sharpe_mean": round(st.mean(values("sharpe")), 4),
        "sharpe_min": round(min(values("sharpe")), 4),
        "mdd_mean": round(st.mean(values("mdd")), 6),
        "mdd_worst": round(min(values("mdd")), 6),
        "days_mean": round(st.mean(values("days")), 1),
        "phases": records,
    }


def _corr(a: pl.DataFrame, b: pl.DataFrame) -> float:
    joined = (
        a.rename({"value": "a"})
        .join(b.rename({"value": "b"}), on="date", how="inner")
        .sort("date")
    )
    va = joined["a"].to_list()
    vb = joined["b"].to_list()
    ra = [va[i] / va[i - 1] - 1.0 for i in range(1, len(va))]
    rb = [vb[i] / vb[i - 1] - 1.0 for i in range(1, len(vb))]
    if len(ra) < 20 or st.pstdev(ra) == 0 or st.pstdev(rb) == 0:
        return 1.0
    return st.correlation(ra, rb)


def main() -> int:
    membership = _historical_membership()
    service = _service()
    phase_outputs = {}
    recent_outputs = {}

    for phase in PHASE_STARTS + RECENT_STARTS:
        key = phase.isoformat()
        curves = {}
        for label, strategy_id in SLEEVES.items():
            curves[label] = _run_sleeve(service, membership, strategy_id=strategy_id, start=phase, end=END)
        fixed, _ = _combine(curves["A_macro_gate_quality"], curves["B_industry_relative_strength_h20_sl20"], "fixed")
        risk, weights = _combine(curves["A_macro_gate_quality"], curves["B_industry_relative_strength_h20_sl20"], "risk")
        rec = {
            "phase": key,
            "A_macro_gate_quality": _stats(curves["A_macro_gate_quality"]),
            "B_industry_relative_strength_h20_sl20": _stats(curves["B_industry_relative_strength_h20_sl20"]),
            "fixed_50_50": _stats(fixed),
            "risk_parity": _stats(risk),
            "correlation": round(_corr(curves["A_macro_gate_quality"], curves["B_industry_relative_strength_h20_sl20"]), 4),
            "risk_parity_weight_a_mean": round(st.mean(row["weight_a"] for row in weights), 4),
            "risk_parity_weight_a_min": round(min(row["weight_a"] for row in weights), 4),
            "risk_parity_weight_a_max": round(max(row["weight_a"] for row in weights), 4),
        }
        if phase in PHASE_STARTS:
            phase_outputs[key] = rec
        else:
            recent_outputs[key] = rec
        print(
            f"[{key}] A={rec['A_macro_gate_quality']['total_return']:+.3f} "
            f"B={rec['B_industry_relative_strength_h20_sl20']['total_return']:+.3f} "
            f"fixed={rec['fixed_50_50']['total_return']:+.3f} "
            f"risk={rec['risk_parity']['total_return']:+.3f} "
            f"corr={rec['correlation']}",
            flush=True,
        )

    long = {
        name: _aggregate([phase_outputs[key][name] for key in sorted(phase_outputs)])
        for name in (
            "A_macro_gate_quality",
            "B_industry_relative_strength_h20_sl20",
            "fixed_50_50",
            "risk_parity",
        )
    }
    recent = {
        name: _aggregate([recent_outputs[key][name] for key in sorted(recent_outputs)])
        for name in (
            "A_macro_gate_quality",
            "B_industry_relative_strength_h20_sl20",
            "fixed_50_50",
            "risk_parity",
        )
    }
    corr_mean = round(st.mean(phase_outputs[key]["correlation"] for key in phase_outputs), 4)
    weight_mean = round(st.mean(phase_outputs[key]["risk_parity_weight_a_mean"] for key in phase_outputs), 4)
    weight_min = round(min(phase_outputs[key]["risk_parity_weight_a_min"] for key in phase_outputs), 4)
    weight_max = round(max(phase_outputs[key]["risk_parity_weight_a_max"] for key in phase_outputs), 4)

    d1 = len(phase_outputs) == 10 and len(recent_outputs) == 5
    e1 = bool(
        long["risk_parity"]["annual_mean"] >= 0.10
        and long["risk_parity"]["sharpe_mean"] >= 0.70
        and long["risk_parity"]["mdd_mean"] >= -0.20
        and long["risk_parity"]["mdd_worst"] >= -0.25
    )
    e2 = long["risk_parity"]["positive_phases"] == 10
    e3 = bool(
        recent["risk_parity"]["total_return_mean"] > 0
        and recent["risk_parity"]["positive_phases"] >= 4
    )
    e4 = bool(
        long["risk_parity"]["sharpe_mean"] >= long["fixed_50_50"]["sharpe_mean"] + 0.03
        and long["risk_parity"]["sharpe_mean"] >= long["A_macro_gate_quality"]["sharpe_mean"] + 0.03
        and long["risk_parity"]["mdd_mean"] >= long["A_macro_gate_quality"]["mdd_mean"] - 0.03
    )
    e5 = corr_mean < 0.80
    e6 = bool(0.25 <= weight_mean <= 0.75)
    decision = {
        "D1": d1,
        "E1": e1,
        "E2": e2,
        "E3": e3,
        "E4": e4,
        "E5": e5,
        "E6": e6,
    }
    decision["all_pass"] = all(decision.values())

    result = {
        "purpose": "R112 dual-sleeve risk parity under daily historical HS300 membership",
        "universe": "daily historical HS300 membership snapshots",
        "window": {"start": str(START), "end": str(END), "phases": [str(x) for x in PHASE_STARTS]},
        "recent_phase_starts": [str(x) for x in RECENT_STARTS],
        "sleeves": SLEEVES,
        "portfolio": {
            "rebalance": "monthly",
            "risk_window": 60,
            "weight_bounds": [0.2, 0.8],
            "round_trip_cost": ROUND_TRIP_COST,
        },
        "long": long,
        "recent": recent,
        "phase_records": phase_outputs,
        "recent_records": recent_outputs,
        "correlation_mean": corr_mean,
        "risk_parity_weight_a": {
            "mean": weight_mean,
            "min": weight_min,
            "max": weight_max,
        },
        "decision": decision,
        "final_recommendation": (
            "R112 dual-sleeve risk parity candidate for forward paper trading"
            if decision["all_pass"]
            else None
        ),
        "caveats": [
            "The industry sleeve still uses current industry snapshots backfilled over history.",
            "Recent fresh-start paths may share the same macro-gate exposure period.",
            "The 3x cost convention is a stress test, not an execution-cost estimate.",
            "No production approval is implied by this evidence.",
        ],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", OUT, flush=True)
    print("decision", decision, flush=True)
    print("long risk", long["risk_parity"], flush=True)
    print("recent risk", recent["risk_parity"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())