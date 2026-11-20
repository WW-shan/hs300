"""R378: cost-stress robustness of the R377 sealed-window result."""
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
PREREG = EVIDENCE / "r378_preregistration.json"
R377 = EVIDENCE / "r377_holdout.json"
OUTPUT = EVIDENCE / "r378_cost_stress.json"
RESULT = EVIDENCE / "r378_result.md"
CENTRE = "fade05_h30_sl20"
CONTROL = "fade05_h20_sl20"
SEALED_START = date(2025, 5, 6)
SEALED_END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
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


def segment(frame: pl.DataFrame, start: date, end: date) -> dict[str, Any]:
    part = frame.filter(pl.col("date").is_between(start, end)).sort("date")
    values = [float(value) for value in part["value"].to_list()]
    return {**stats(values), "start": str(part["date"][0]), "end": str(part["date"][-1])}


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r378_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r378_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r378_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r378_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r378_r114"),
    }


def compose(curve: pl.DataFrame, ctx: dict[str, Any], cost: str) -> pl.DataFrame:
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r359 = ctx["r359"]
    r112 = ctx["r112"]
    r114 = ctx["r114"]
    scenario = SCENARIOS[cost]
    a = _curve(RESEARCH / f"r344_cache/ai_hs300_macro_gate_quality_2022-05-05_{cost}_p10.parquet")
    starts = (date(2022, 5, 5), date(2022, 6, 2), date(2022, 7, 1))
    c = r359.combine_cohort_curves(
        [
            _curve(RESEARCH / f"r359_v2_cache/ai_hs300_quality_momentum_broad_h60_{start}_{cost}_p50.parquet")
            for start in starts
        ],
        list(starts),
    )
    gold_proxy = r344.gold_trend_proxy(
        r331.asset_frame("gold", "518880.SH"),
        r331.asset_frame("treasury", "511260.SH"),
        r331.asset_frame("dividend", "510880.SH"),
        scenario["defensive_turnover"],
    )
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
        risk, _ = r344._combine_three((a, curve, c), scenario["portfolio_turnover"], 0.10)
        equity = r114._vol_target(risk)["curve"]
        return r331.build_three_asset_bucket(
            equity,
            gold_proxy,
            r331.asset_frame("treasury", "511260.SH"),
            r331.asset_frame("dividend", "510880.SH"),
            round_trip_cost=scenario["defensive_turnover"],
        )["curve"]
    finally:
        r112.ROUND_TRIP_COST = old_112
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    basis = json.loads(R377.read_text(encoding="utf-8"))
    ctx = _context()
    rows: dict[str, Any] = {}
    for cost in SCENARIOS:
        centre = compose(_curve(RESEARCH / f"r371_cache/{CENTRE}.parquet"), ctx, cost)
        control = compose(_curve(RESEARCH / f"r371_cache/{CONTROL}.parquet"), ctx, cost)
        rows[cost] = {
            CENTRE: segment(centre, SEALED_START, SEALED_END),
            CONTROL: segment(control, SEALED_START, SEALED_END),
            "recent": {
                CENTRE: segment(centre, RECENT_START, SEALED_END),
                CONTROL: segment(control, RECENT_START, SEALED_END),
            },
        }
    base = rows["2x"]
    if not math.isclose(base[CENTRE]["total_return"], basis["sealed"][CENTRE]["total_return"], abs_tol=1e-9):
        raise RuntimeError("R378 2x centre does not reproduce R377")
    if not math.isclose(base[CONTROL]["total_return"], basis["sealed"][CONTROL]["total_return"], abs_tol=1e-9):
        raise RuntimeError("R378 2x control does not reproduce R377")
    three = rows["3x"]
    checks = {
        "centre_3x_positive": three[CENTRE]["total_return"] > 0.0,
        "centre_3x_sharpe": three[CENTRE]["sharpe"] >= 0.80,
        "centre_3x_mdd": three[CENTRE]["mdd"] >= -0.12,
        "centre_3x_beats_control_return": three[CENTRE]["total_return"] > three[CONTROL]["total_return"],
        "centre_3x_beats_control_sharpe": three[CENTRE]["sharpe"] >= three[CONTROL]["sharpe"],
    }
    five = rows["5x"]
    return {
        "round": "R378",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "source_r377_sha256": sha256(R377),
        "sealed_window": [str(SEALED_START), str(SEALED_END)],
        "cost_scenarios": SCENARIOS,
        "rows": rows,
        "checks_3x": checks,
        "robust_3x": all(checks.values()),
        "five_x_diagnostic": {
            "centre": five[CENTRE],
            "control": five[CONTROL],
            "hard_rejection": False,
        },
        "decision": {
            "sealed_advantage_robust_at_3x": all(checks.values()),
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# R378 · 封存窗口成本压力复核",
        "",
        f"窗口：{report['sealed_window'][0]} 至 {report['sealed_window'][1]}。",
        "",
        "| 成本 | 曲线 | 总收益 | 年化 | Sharpe | MDD |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for cost, row in report["rows"].items():
        for cell, label in ((CENTRE, "hold-30 中心"), (CONTROL, "20 日对照")):
            item = row[cell]
            lines.append(
                f"| {cost} | {label} | {item['total_return']:.2%} | {item['annual']:.2%} | "
                f"{item['sharpe']:.3f} | {item['mdd']:.2%} |"
            )
    recent = report["rows"]["3x"]["recent"]
    lines.extend([
        "",
        f"- 3× 封存段最后一年：中心 {recent[CENTRE]['total_return']:+.2%}，对照 {recent[CONTROL]['total_return']:+.2%}。",
        f"- 5× 中心年化 {report['five_x_diagnostic']['centre']['annual']:.2%}、"
        f"Sharpe {report['five_x_diagnostic']['centre']['sharpe']:.3f}、"
        f"MDD {report['five_x_diagnostic']['centre']['mdd']:.2%}；仅诊断，不作硬否决。",
        "",
        "| 3× 门槛 | 结果 |",
        "| --- | :---: |",
    ])
    for name, value in report["checks_3x"].items():
        lines.append(f"| {name} | {value} |")
    lines.extend([
        "",
        f"**3× 稳健性：{'通过' if report['robust_3x'] else '未通过'}。**",
        "",
        "- 封存段仅约 17 个月，且与 2025-2026 市场状态重叠。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R378 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    print(json.dumps(report["checks_3x"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
