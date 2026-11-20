"""R375: walk-forward test of platform selection versus chasing trailing performance.

This round does not retune the strategy. It replays the frozen 27-cell R371
B-sleeve curves through the same portfolio pipeline and evaluates only the
selection rule at each monthly decision point, using trailing information only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
PREREG = EVIDENCE / "r375_preregistration.json"
R371 = EVIDENCE / "r371_alpha_parameter_grid.json"
OUTPUT = EVIDENCE / "r375_platform_walkforward.json"
RESULT = EVIDENCE / "r375_result.md"
CACHE = RESEARCH / "r371_cache"
START = date(2022, 5, 5)
END = date(2026, 11, 5)
TRAILING = 252
FORWARD = 252
FIXED_CENTRE = "fade05_h30_sl20"
MIN_PLATFORM_CELLS = 5
ROUND_TRIP_COST = 0.0052
DEFENSIVE_TURNOVER_COST = 0.001
FADES = (3, 5, 10)
HOLDS = (10, 20, 30)
STOPS = (-0.15, -0.20, -0.25)
POLICIES = (
    "P1_trailing_best_annual",
    "P2_trailing_best_sharpe",
    "P3_platform_medoid",
    "P4_maximin_neighbour",
    "P5_fixed_centre",
)
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


def _curve(path: Path) -> pl.DataFrame:
    frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date)).sort("date")
    if frame.columns != ["date", "value"]:
        raise ValueError(f"unexpected curve schema: {path.name}")
    return frame


def curve_stats(values: list[float]) -> dict[str, float | int]:
    if len(values) < 2:
        raise ValueError("curve window too short")
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


def _passes_v2(metrics: dict[str, Any]) -> bool:
    return bool(
        metrics["annual"] >= 0.08
        and metrics["sharpe"] >= 1.0
        and metrics["mdd"] >= -0.20
    )


def axis_neighbours(cell: str) -> tuple[str, ...]:
    """Return the immediate one-axis neighbours inside the declared grid.

    The grid levels are not equally spaced in raw units (holds 10/20/30,
    stops -15/-20/-25%), so neighbours must be defined by grid index rather
    than by arithmetic on the level value.
    """
    fade_index, hold_index, stop_index = _param_index(cell)
    neighbours = []
    for delta in (-1, 1):
        if 0 <= fade_index + delta < len(FADES):
            neighbours.append(cell_slug(FADES[fade_index + delta], HOLDS[hold_index], STOPS[stop_index]))
        if 0 <= hold_index + delta < len(HOLDS):
            neighbours.append(cell_slug(FADES[fade_index], HOLDS[hold_index + delta], STOPS[stop_index]))
        if 0 <= stop_index + delta < len(STOPS):
            neighbours.append(cell_slug(FADES[fade_index], HOLDS[hold_index], STOPS[stop_index + delta]))
    return tuple(dict.fromkeys(neighbours))


def _param_index(cell: str) -> tuple[int, int, int]:
    fade, hold, stop = cell_params(cell)
    return FADES.index(fade), HOLDS.index(hold), STOPS.index(stop)


def parameter_distance(left: str, right: str) -> int:
    a = _param_index(left)
    b = _param_index(right)
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def trailing_metrics(curves: dict[str, pl.DataFrame], decision: date) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for cell, curve in curves.items():
        window = curve.filter(pl.col("date") <= decision).sort("date")
        if window.height < TRAILING + 1:
            raise ValueError(f"{cell} has fewer than {TRAILING} trailing returns at {decision}")
        values = [float(value) for value in window["value"].tail(TRAILING + 1).to_list()]
        output[cell] = curve_stats(values)
    return output


def select_trailing_best_annual(metrics: dict[str, dict[str, Any]]) -> str:
    return max(sorted(metrics), key=lambda cell: (metrics[cell]["total_return"], metrics[cell]["sharpe"], cell))


def select_trailing_best_sharpe(metrics: dict[str, dict[str, Any]]) -> str:
    return max(sorted(metrics), key=lambda cell: (metrics[cell]["sharpe"], metrics[cell]["total_return"], cell))


def select_platform_medoid(metrics: dict[str, dict[str, Any]]) -> str:
    passing = [cell for cell in sorted(metrics) if _passes_v2(metrics[cell])]
    if len(passing) < MIN_PLATFORM_CELLS:
        return FIXED_CENTRE
    def score(cell: str) -> tuple[float, float, str]:
        spread = sum(parameter_distance(cell, other) for other in passing)
        return (spread, -float(metrics[cell]["sharpe"]), cell)
    return min(passing, key=score)


def select_maximin_neighbour(metrics: dict[str, dict[str, Any]]) -> str:
    def score(cell: str) -> tuple[float, float, str]:
        neighbours = [metrics[other]["sharpe"] for other in axis_neighbours(cell)]
        floor = min(neighbours) if neighbours else float(metrics[cell]["sharpe"])
        return (floor, float(metrics[cell]["sharpe"]), cell)
    return max(sorted(metrics), key=score)


SELECTORS: dict[str, Callable[[dict[str, dict[str, Any]]], str]] = {
    "P1_trailing_best_annual": select_trailing_best_annual,
    "P2_trailing_best_sharpe": select_trailing_best_sharpe,
    "P3_platform_medoid": select_platform_medoid,
    "P4_maximin_neighbour": select_maximin_neighbour,
    "P5_fixed_centre": lambda metrics: FIXED_CENTRE,
}


def decision_dates(sessions: list[date]) -> list[date]:
    points = []
    for index, day in enumerate(sessions):
        if index >= TRAILING and index + FORWARD < len(sessions) and day.month != sessions[index - 1].month:
            points.append(day)
    return points


def forward_return(curve: pl.DataFrame, decision: date) -> float:
    window = curve.filter(pl.col("date") >= decision).sort("date").head(FORWARD + 1)
    if window.height != FORWARD + 1:
        raise ValueError(f"forward window from {decision} is incomplete")
    values = window["value"].to_list()
    return float(values[-1] / values[0] - 1.0)


def forward_mdd(curve: pl.DataFrame, decision: date) -> float:
    window = curve.filter(pl.col("date") >= decision).sort("date").head(FORWARD + 1)
    if window.height != FORWARD + 1:
        raise ValueError(f"forward window from {decision} is incomplete")
    values = [float(value) for value in window["value"].to_list()]
    peak = values[0]
    mdd = 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)
    return mdd


def spearman(left: list[float], right: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
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
    ranked_left = ranks(left)
    ranked_right = ranks(right)
    mean_left = st.mean(ranked_left)
    mean_right = st.mean(ranked_right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(ranked_left, ranked_right, strict=True))
    denominator = math.sqrt(
        sum((a - mean_left) ** 2 for a in ranked_left)
        * sum((b - mean_right) ** 2 for b in ranked_right)
    )
    return numerator / denominator if denominator else 0.0


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r375_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r375_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r375_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r375_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r375_r114"),
    }


def compose(curve: pl.DataFrame, ctx: dict[str, Any]) -> pl.DataFrame:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r359 = ctx["r359"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    a = _curve(RESEARCH / "r344_cache/ai_hs300_macro_gate_quality_2022-05-05_2x_p10.parquet")
    starts = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
    cohorts = [
        _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_2x_p50.parquet")
        for start in starts
    ]
    c = r359.combine_cohort_curves(cohorts, list(starts))
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
        risk, _ = r344._combine_three((a, curve, c), ROUND_TRIP_COST, 0.10)
        equity = r114._vol_target(risk)["curve"]
        gold_proxy = r344.gold_trend_proxy(
            r331.asset_frame("gold", "518880.SH"),
            r331.asset_frame("treasury", "511260.SH"),
            r331.asset_frame("dividend", "510880.SH"),
            DEFENSIVE_TURNOVER_COST,
        )
        return r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            r331.asset_frame("treasury", "511260.SH"),
            r331.asset_frame("dividend", "510880.SH"),
            round_trip_cost=DEFENSIVE_TURNOVER_COST,
        )["curve"]
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    cells = grid_cells()
    expected = {cell_slug(fade, hold, stop) for fade in FADES for hold in HOLDS for stop in STOPS}
    if set(cells) != expected or len(cells) != 27:
        raise ValueError("R375 grid does not match 27-cell definition")
    source_manifest = {cell: sha256(CACHE / f"{cell}.parquet") for cell in cells}
    ctx = _context()
    composed = {cell: compose(_curve(CACHE / f"{cell}.parquet"), ctx) for cell in cells}
    base = composed[FIXED_CENTRE]
    sessions = base["date"].to_list()
    decisions = decision_dates(sessions)
    if not decisions:
        raise ValueError("no walk-forward decision dates")
    records = []
    for decision in decisions:
        trailing = trailing_metrics(composed, decision)
        selected = {name: SELECTORS[name](trailing) for name in POLICIES}
        forward = {cell: forward_return(composed[cell], decision) for cell in cells}
        forward_mdds = {cell: forward_mdd(composed[cell], decision) for cell in cells}
        records.append({
            "decision": str(decision),
            "trailing_cells_passing": sum(_passes_v2(metrics) for metrics in trailing.values()),
            "selected": selected,
            "trailing_stats": {
                cell: {
                    "annual": trailing[cell]["annual"],
                    "sharpe": trailing[cell]["sharpe"],
                    "mdd": trailing[cell]["mdd"],
                    "total_return": trailing[cell]["total_return"],
                }
                for cell in cells
            },
            "forward_returns": forward,
            "forward_mdds": forward_mdds,
            "rank_correlation": spearman(
                [trailing[cell]["total_return"] for cell in cells],
                [forward[cell] for cell in cells],
            ),
        })
    policy_results: dict[str, Any] = {}
    for name in POLICIES:
        realized = [record["forward_returns"][record["selected"][name]] for record in records]
        mdds = [record["forward_mdds"][record["selected"][name]] for record in records]
        choices = [record["selected"][name] for record in records]
        modal = max(sorted(set(choices)), key=lambda cell: (choices.count(cell), cell))
        policy_results[name] = {
            "decisions": len(realized),
            "mean_forward_return": st.mean(realized),
            "median_forward_return": st.median(realized),
            "worst_forward_return": min(realized),
            "negative_windows": sum(value < 0 for value in realized),
            "mean_forward_mdd": st.mean(mdds),
            "worst_forward_mdd": min(mdds),
            "distinct_cells": len(set(choices)),
            "modal_cell": modal,
            "modal_share": choices.count(modal) / len(choices),
            "choices": choices,
            "realized_forward_returns": realized,
        }
    correlations = [record["rank_correlation"] for record in records]
    p1 = policy_results["P1_trailing_best_annual"]
    p3 = policy_results["P3_platform_medoid"]
    p5 = policy_results["P5_fixed_centre"]
    supported = bool(
        p3["mean_forward_return"] > p1["mean_forward_return"]
        and p3["worst_forward_return"] > p1["worst_forward_return"]
    )
    fixed_dominates = bool(
        p5["mean_forward_return"] >= max(policy_results[name]["mean_forward_return"] for name in POLICIES if name != "P5_fixed_centre")
        and p5["worst_forward_return"] >= max(policy_results[name]["worst_forward_return"] for name in POLICIES if name != "P5_fixed_centre")
    )
    if supported:
        verdict = "platform_selection_supported"
    elif fixed_dominates:
        verdict = "no_ex_ante_selector_adds_value_fixed_centre_remains_default"
    else:
        verdict = "platform_claim_limited_to_parameter_robustness"
    p4 = policy_results["P4_maximin_neighbour"]
    p4_beats_trailing = bool(
        p4["mean_forward_return"] > p1["mean_forward_return"]
        and p4["worst_forward_return"] > p1["worst_forward_return"]
    )
    return {
        "round": "R375",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "source_manifest": source_manifest,
        "grid": list(cells),
        "decision_points": len(records),
        "trailing_length": TRAILING,
        "forward_length": FORWARD,
        "fixed_centre": FIXED_CENTRE,
        "rank_correlation": {
            "mean": st.mean(correlations),
            "median": st.median(correlations),
            "min": min(correlations),
            "max": max(correlations),
        },
        "policies": policy_results,
        "decision": {
            "platform_selection_supported": supported,
            "fixed_centre_dominates": fixed_dominates,
            "secondary_maximin_beats_trailing_on_mean_and_worst": p4_beats_trailing,
            "secondary_maximin_note": (
                "P4 is one of five correlated policies and does not beat the fixed centre on mean forward return; "
                "its better worst case is reported but not promoted as a new selection rule."
            ),
            "verdict": verdict,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "records": records,
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R375 · 平台中心与追逐过去一年第一的真实前向比较",
        "",
        f"结论：**{report['decision']['verdict']}**。决策点 {report['decision_points']} 个，"
        f"每期只用过去 {TRAILING} 个交易日选择，随后看未来 {FORWARD} 个交易日真实净值。",
        "",
        "| 选择规则 | 平均前向收益 | 中位数 | 最差 | 负窗口 | 平均前向MDD | 最差前向MDD | 选择过格数 | 众数占比 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    labels = {
        "P1_trailing_best_annual": "追过去一年收益第一",
        "P2_trailing_best_sharpe": "追过去一年Sharpe第一",
        "P3_platform_medoid": "平台几何中心",
        "P4_maximin_neighbour": "最大化最弱邻居",
        "P5_fixed_centre": "固定平台中心",
    }
    for name in POLICIES:
        item = report["policies"][name]
        lines.append(
            f"| {labels[name]} | {item['mean_forward_return']:.2%} | {item['median_forward_return']:.2%} | "
            f"{item['worst_forward_return']:.2%} | {item['negative_windows']}/{item['decisions']} | "
            f"{item['mean_forward_mdd']:.2%} | {item['worst_forward_mdd']:.2%} | "
            f"{item['distinct_cells']} | {item['modal_share']:.1%} |"
        )
    rc = report["rank_correlation"]
    lines.extend([
        "",
        f"- 过去收益与未来收益的截面 Spearman 秩相关：均值 {rc['mean']:.3f}，中位数 {rc['median']:.3f}，范围 {rc['min']:.3f} 至 {rc['max']:.3f}。",
        f"- 平台选择规则通过平均收益和最差收益双重条件：{report['decision']['platform_selection_supported']}。",
        f"- 固定中心同时不劣于其他所有规则：{report['decision']['fixed_centre_dominates']}。",
        f"- 次要规则“最大化最弱邻居”在平均和最差前向收益上都优于追过去一年第一：{report['decision']['secondary_maximin_beats_trailing_on_mean_and_worst']}。它仍未超过固定中心平均收益，且是 5 条相关规则中的 1 条，本轮不提升为新选择规则。",
        "- 27 格和 hold-30 区域均已在 R371/R373/R374 观察，本轮是事后选择规则检验，不是独立 alpha 发现。",
        "- 前向窗口重叠，名义决策点远多于有效独立样本；不得据此声称 PBO/DSR 已通过。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R375 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
