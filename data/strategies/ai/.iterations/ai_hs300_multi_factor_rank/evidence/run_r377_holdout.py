"""R377: pre-registered sealed-window evaluation of the R374 hold-30 centre."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "CONTRIBUTING.md").is_file())
EVIDENCE = Path(__file__).resolve().parent
DATA = ROOT / "data"
RESEARCH = DATA / "research"
PREREG = EVIDENCE / "r377_preregistration.json"
R374 = EVIDENCE / "r374_platform_validation.json"
OUTPUT = EVIDENCE / "r377_holdout.json"
RESULT = EVIDENCE / "r377_result.md"
CACHE = RESEARCH / "r371_cache"
CENTRE = "fade05_h30_sl20"
CONTROL = "fade05_h20_sl20"
DEVELOPMENT_END = date(2025, 5, 5)
SEALED_START = date(2025, 5, 6)
SEALED_END = date(2026, 11, 5)
RECENT_SEALED_START = date(2025, 11, 5)
ROUND_TRIP_COST = 0.0052
DEFENSIVE_COST = 0.001
FADES = (3, 5, 10)
HOLDS = (10, 20, 30)
STOPS = (-0.15, -0.20, -0.25)
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


def grid_cells() -> tuple[str, ...]:
    return tuple(cell_slug(fade, hold, stop) for fade in FADES for hold in HOLDS for stop in STOPS)


def _curve(path: Path) -> pl.DataFrame:
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
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r377_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r377_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r377_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r377_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r377_r114"),
    }


def compose(curve: pl.DataFrame, ctx: dict[str, Any]) -> pl.DataFrame:
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
    gold_proxy = r344.gold_trend_proxy(
        r331.asset_frame("gold", "518880.SH"),
        r331.asset_frame("treasury", "511260.SH"),
        r331.asset_frame("dividend", "510880.SH"),
        DEFENSIVE_COST,
    )
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
        return r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            r331.asset_frame("treasury", "511260.SH"),
            r331.asset_frame("dividend", "510880.SH"),
            round_trip_cost=DEFENSIVE_COST,
        )["curve"]
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def segment(frame: pl.DataFrame, start: date, end: date) -> dict[str, Any]:
    part = frame.filter(pl.col("date").is_between(start, end)).sort("date")
    if part.height < 3:
        raise ValueError(f"segment {start}..{end} is too short")
    values = [float(value) for value in part["value"].to_list()]
    return {**stats(values), "start": str(part["date"][0]), "end": str(part["date"][-1])}


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen = json.loads(R374.read_text(encoding="utf-8"))
    expected = next(row for row in frozen["rows"] if row["cell"] == CENTRE)
    cells = grid_cells()
    if len(cells) != 27:
        raise ValueError("R377 requires the 27-cell grid")
    ctx = _context()
    composed = {cell: compose(_curve(CACHE / f"{cell}.parquet"), ctx) for cell in cells}
    # Confirm the frozen pipeline before evaluating the sealed window.
    full = stats(composed[CENTRE]["value"].to_list())
    if not math.isclose(full["annual"], expected["annual_2x"], abs_tol=1e-9):
        raise RuntimeError("R377 full-window centre does not reconstruct R374")
    development = {cell: segment(composed[cell], date(2022, 5, 5), DEVELOPMENT_END) for cell in cells}
    sealed = {cell: segment(composed[cell], SEALED_START, SEALED_END) for cell in cells}
    recent_sealed = {
        cell: segment(composed[cell], RECENT_SEALED_START, SEALED_END) for cell in cells
    }
    centre_sealed = sealed[CENTRE]
    control_sealed = sealed[CONTROL]
    centre_dev = development[CENTRE]
    control_dev = development[CONTROL]
    persistence = spearman(
        [development[cell]["annual"] for cell in cells],
        [sealed[cell]["annual"] for cell in cells],
    )
    dev_top = max(cells, key=lambda cell: development[cell]["annual"])
    sealed_top = max(cells, key=lambda cell: sealed[cell]["annual"])
    gates = {
        "centre_sealed_positive": centre_sealed["total_return"] > 0.0,
        "centre_sealed_sharpe": centre_sealed["sharpe"] >= 1.0,
        "centre_sealed_mdd": centre_sealed["mdd"] >= -0.10,
        "centre_beats_control": centre_sealed["total_return"] > control_sealed["total_return"],
        "centre_beats_control_sharpe": centre_sealed["sharpe"] >= control_sealed["sharpe"],
        "ranking_persistence": persistence >= 0.30,
    }
    return {
        "round": "R377",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "centre": CENTRE,
        "control": CONTROL,
        "development_window": ["2022-05-05", str(DEVELOPMENT_END)],
        "sealed_window": [str(SEALED_START), str(SEALED_END)],
        "development": {CENTRE: centre_dev, CONTROL: control_dev},
        "sealed": {CENTRE: centre_sealed, CONTROL: control_sealed},
        "recent_sealed": {CENTRE: recent_sealed[CENTRE], CONTROL: recent_sealed[CONTROL]},
        "sealed_excess_total_return": centre_sealed["total_return"] - control_sealed["total_return"],
        "sealed_excess_sharpe": centre_sealed["sharpe"] - control_sealed["sharpe"],
        "development_top_cell": dev_top,
        "sealed_top_cell": sealed_top,
        "ranking_persistence": persistence,
        "gates": gates,
        "all_pass": all(gates.values()),
        "cells": {
            cell: {"development": development[cell], "sealed": sealed[cell]}
            for cell in cells
        },
        "decision": {
            "holdout_claim_supported": all(gates.values()),
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    centre = report["sealed"][report["centre"]]
    control = report["sealed"][report["control"]]
    lines = [
        "# R377 · hold-30 平台中心封存窗口检验",
        "",
        f"开发窗口 {report['development_window'][0]} 至 {report['development_window'][1]}，"
        f"封存窗口 {report['sealed_window'][0]} 至 {report['sealed_window'][1]}。中心在封存前已固定为 `{report['centre']}`。",
        "",
        "| 曲线 | 封存总收益 | 年化 | Sharpe | MDD | 天数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        f"| hold-30 中心 | {centre['total_return']:.2%} | {centre['annual']:.2%} | {centre['sharpe']:.3f} | {centre['mdd']:.2%} | {centre['days']} |",
        f"| 20 日对照 | {control['total_return']:.2%} | {control['annual']:.2%} | {control['sharpe']:.3f} | {control['mdd']:.2%} | {control['days']} |",
        "",
        f"封存段最后一年（2025-11-05 至 2026-11-05）：hold-30 中心总收益 {report['recent_sealed'][report['centre']]['total_return']:+.2%}、Sharpe {report['recent_sealed'][report['centre']]['sharpe']:.3f}；20 日对照 {report['recent_sealed'][report['control']]['total_return']:+.2%}、Sharpe {report['recent_sealed'][report['control']]['sharpe']:.3f}。",
        "",
        f"- 中心相对对照的封存超额：总收益 {report['sealed_excess_total_return']:+.2%}，Sharpe {report['sealed_excess_sharpe']:+.3f}。",
        f"- 开发窗口第一名：`{report['development_top_cell']}`；封存窗口第一名：`{report['sealed_top_cell']}`。",
        f"- 27 格开发窗口与封存窗口年化的秩相关：{report['ranking_persistence']:.3f}。",
        "",
        "| 门槛 | 结果 |",
        "| --- | :---: |",
    ]
    for name, value in report["gates"].items():
        lines.append(f"| {name} | {value} |")
    lines.extend([
        "",
        f"**总判定：{'通过' if report['all_pass'] else '未通过'}。**",
        "",
        "- 27 格网格在 R371 已全窗口观察，本轮只是时间切分重读同一批曲线，不是新生成数据。",
        "- 封存窗口只有一段约 17 个月，排名持续性门槛在单段噪声下容易失败，失败本身不等于策略退化。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R377 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    print(json.dumps(report["gates"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
