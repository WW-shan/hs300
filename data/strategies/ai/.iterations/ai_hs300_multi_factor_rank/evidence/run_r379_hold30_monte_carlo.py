"""R379: portfolio-parameter Monte Carlo on the sealed hold-30 centre."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import random
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
PREREG = EVIDENCE / "r379_preregistration.json"
R377 = EVIDENCE / "r377_holdout.json"
OUTPUT = EVIDENCE / "r379_hold30_monte_carlo.json"
RESULT = EVIDENCE / "r379_result.md"
CENTRE = "fade05_h30_sl20"
CONTROL = "fade05_h20_sl20"
FULL_START = date(2022, 5, 5)
FULL_END = date(2026, 11, 5)
SEALED_START = date(2025, 5, 6)
SEALED_END = date(2026, 11, 5)
RECENT_START = date(2025, 11, 5)
ROUND_TRIP_COST = 0.0052
DEFENSIVE_COST = 0.001
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


def latin_hypercube(samples: int, dimensions: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in range(dimensions):
        strata = list(range(samples))
        rng.shuffle(strata)
        columns.append([(strata[index] + rng.random()) / samples for index in range(samples)])
    return [[columns[column][row] for column in range(dimensions)] for row in range(samples)]


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


def year_returns(frame: pl.DataFrame) -> dict[str, float]:
    grouped: dict[int, list[float]] = {}
    for row in frame.iter_rows(named=True):
        grouped.setdefault(row["date"].year, []).append(float(row["value"]))
    output: dict[str, float] = {}
    previous: float | None = None
    for year in sorted(grouped):
        values = grouped[year]
        start = previous if previous is not None else values[0]
        output[str(year)] = values[-1] / start - 1.0
        previous = values[-1]
    return output


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(fraction * len(ordered))))]


def _context() -> dict[str, Any]:
    return {
        "r331": module(EVIDENCE / "run_r331_three_asset_bucket.py", "r379_r331"),
        "r344": module(EVIDENCE / "run_r344_extended_window.py", "r379_r344"),
        "r359": module(EVIDENCE / "run_r359_cohort_ensemble.py", "r379_r359"),
        "r112": module(ROOT / "scripts/backtest_r112_dual_sleeve_risk_parity.py", "r379_r112"),
        "r114": module(ROOT / "scripts/backtest_r114_b_vol_target.py", "r379_r114"),
    }


def build_report() -> dict[str, Any]:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    basis = json.loads(R377.read_text(encoding="utf-8"))
    ranges = prereg["design"]["ranges"]
    ctx = _context()
    r331 = ctx["r331"]
    r344 = ctx["r344"]
    r359 = ctx["r359"]
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
    b_centre = _curve(RESEARCH / f"r371_cache/{CENTRE}.parquet")
    b_control = _curve(RESEARCH / f"r371_cache/{CONTROL}.parquet")
    gold_proxy = r344.gold_trend_proxy(
        r331.asset_frame("gold", "518880.SH"),
        r331.asset_frame("treasury", "511260.SH"),
        r331.asset_frame("dividend", "510880.SH"),
        DEFENSIVE_COST,
    )
    treasury = r331.asset_frame("treasury", "511260.SH")
    dividend = r331.asset_frame("dividend", "510880.SH")
    samples = latin_hypercube(
        prereg["design"]["samples"],
        len(ranges),
        prereg["design"]["seed"],
    )
    old_114 = (r114.TARGET_VOL, r114.ROUND_TRIP_COST)
    old_331 = (r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX)
    rows: list[dict[str, Any]] = []
    try:
        r114.ROUND_TRIP_COST = ROUND_TRIP_COST
        for index, unit in enumerate(samples):
            c_cap = ranges["c_satellite_cap"][0] + unit[0] * (
                ranges["c_satellite_cap"][1] - ranges["c_satellite_cap"][0]
            )
            equity_target = ranges["equity_target_vol"][0] + unit[1] * (
                ranges["equity_target_vol"][1] - ranges["equity_target_vol"][0]
            )
            lookback = round(
                ranges["overlay_lookback_days"][0]
                + unit[2] * (ranges["overlay_lookback_days"][1] - ranges["overlay_lookback_days"][0])
            )
            overlay_target = ranges["overlay_target_vol"][0] + unit[3] * (
                ranges["overlay_target_vol"][1] - ranges["overlay_target_vol"][0]
            )
            equity_weight_max = ranges["overlay_equity_weight_max"][0] + unit[4] * (
                ranges["overlay_equity_weight_max"][1] - ranges["overlay_equity_weight_max"][0]
            )
            parameters = {
                "c_satellite_cap": c_cap,
                "equity_target_vol": equity_target,
                "overlay_lookback_days": lookback,
                "overlay_target_vol": overlay_target,
                "overlay_equity_weight_max": equity_weight_max,
            }
            outcome: dict[str, Any] = {"sample": index, "parameters": parameters}
            for label, b in (("centre", b_centre), ("control", b_control)):
                risk, _ = r344._combine_three((a, b, c), ROUND_TRIP_COST, c_cap)
                r114.TARGET_VOL = equity_target
                equity = r114._vol_target(risk)["curve"]
                r331.LOOKBACK = lookback
                r331.TARGET_VOL = overlay_target
                r331.EQUITY_WEIGHT_MAX = equity_weight_max
                final = r331.build_three_asset_bucket(
                    equity,
                    gold_proxy,
                    treasury,
                    dividend,
                    round_trip_cost=DEFENSIVE_COST,
                )["curve"]
                full = stats(final["value"].to_list())
                sealed = segment(final, SEALED_START, SEALED_END)
                recent = segment(final, RECENT_START, SEALED_END)
                outcome[label] = {
                    "full": {**full, "yearly": year_returns(final)},
                    "sealed": sealed,
                    "recent": recent,
                }
            outcome["sealed_advantage"] = (
                outcome["centre"]["sealed"]["total_return"] - outcome["control"]["sealed"]["total_return"]
            )
            rows.append(outcome)
    finally:
        r114.TARGET_VOL, r114.ROUND_TRIP_COST = old_114
        r331.LOOKBACK, r331.TARGET_VOL, r331.EQUITY_WEIGHT_MAX = old_331

    gates = prereg["evaluation"]
    full_pass = []
    sealed_pass = []
    advantage_pass = []
    for row in rows:
        centre = row["centre"]
        full = centre["full"]
        sealed = centre["sealed"]
        recent = centre["recent"]
        full_ok = bool(
            full["annual"] >= gates["full_window_v2"]["annual_min"]
            and full["sharpe"] >= gates["full_window_v2"]["sharpe_min"]
            and full["mdd"] >= gates["full_window_v2"]["mdd_min"]
            and full["yearly"].get("2022", -1.0) >= gates["full_window_v2"]["year_2022_min"]
            and full["yearly"].get("2023", -1.0) >= gates["full_window_v2"]["year_2023_min"]
            and recent["total_return"] > gates["full_window_v2"]["recent_min_exclusive"]
        )
        sealed_ok = bool(
            sealed["total_return"] > 0.0
            and sealed["sharpe"] >= gates["sealed_window"]["centre_sharpe_min"]
            and sealed["mdd"] >= gates["sealed_window"]["centre_mdd_min"]
        )
        advantage_ok = row["sealed_advantage"] > 0.0
        row["full_pass"] = full_ok
        row["sealed_pass"] = sealed_ok
        row["sealed_advantage_pass"] = advantage_ok
        full_pass.append(full_ok)
        sealed_pass.append(sealed_ok)
        advantage_pass.append(advantage_ok)

    # Post-hoc diagnostic only: the pre-registered gate above keeps the strict
    # 2022 >= 0 rule from R374. Gate policy v2 tolerates 2022 >= -5%. Recording
    # both makes the 11 failures interpretable without moving the gate.
    v2_pass = []
    for row in rows:
        full = row["centre"]["full"]
        recent = row["centre"]["recent"]
        v2_pass.append(bool(
            full["annual"] >= 0.08
            and full["sharpe"] >= 1.0
            and full["mdd"] >= -0.20
            and full["yearly"].get("2022", -1.0) >= -0.05
            and full["yearly"].get("2023", -1.0) >= 0.0
            and recent["total_return"] > 0.0
        ))
    failed_rows = [row for row in rows if not row["full_pass"]]
    only_2022 = [
        row for row in failed_rows
        if row["centre"]["full"]["annual"] >= 0.08
        and row["centre"]["full"]["sharpe"] >= 1.0
        and row["centre"]["full"]["mdd"] >= -0.20
        and row["centre"]["full"]["yearly"].get("2022", -1.0) >= -0.05
        and row["centre"]["full"]["yearly"].get("2023", -1.0) >= 0.0
        and row["centre"]["recent"]["total_return"] > 0.0
    ]
    full_rate = sum(full_pass) / len(rows)
    sealed_rate = sum(sealed_pass) / len(rows)
    advantage_rate = sum(advantage_pass) / len(rows)
    base_centre = basis["sealed"][CENTRE]
    base_control = basis["sealed"][CONTROL]
    centre_sealed = [row["centre"]["sealed"]["total_return"] for row in rows]
    control_sealed = [row["control"]["sealed"]["total_return"] for row in rows]
    centre_adv = [row["sealed_advantage"] for row in rows]
    plateau = gates["plateau"]
    robust = bool(
        full_rate >= plateau["minimum_full_pass_rate"]
        and sealed_rate >= plateau["minimum_sealed_pass_rate"]
        and advantage_rate >= plateau["minimum_sealed_advantage_rate"]
    )
    return {
        "round": "R379",
        "classification": prereg["classification"],
        "preregistration_sha256": sha256(PREREG),
        "source_r377_sha256": sha256(R377),
        "centre": CENTRE,
        "control": CONTROL,
        "samples": len(rows),
        "ranges": ranges,
        "baseline": {"centre": base_centre, "control": base_control},
        "summary": {
            "full_pass_count": sum(full_pass),
            "full_pass_rate": full_rate,
            "sealed_pass_count": sum(sealed_pass),
            "sealed_pass_rate": sealed_rate,
            "sealed_advantage_count": sum(advantage_pass),
            "sealed_advantage_rate": advantage_rate,
            "centre_sealed_return_p05_median_p95": [
                quantile(centre_sealed, 0.05),
                quantile(centre_sealed, 0.50),
                quantile(centre_sealed, 0.95),
            ],
            "control_sealed_return_p05_median_p95": [
                quantile(control_sealed, 0.05),
                quantile(control_sealed, 0.50),
                quantile(control_sealed, 0.95),
            ],
            "advantage_p05_median_p95": [
                quantile(centre_adv, 0.05),
                quantile(centre_adv, 0.50),
                quantile(centre_adv, 0.95),
            ],
            "advantage_worst": min(centre_adv),
            "advantage_best": max(centre_adv),
            "posthoc_gate_policy_v2_full_pass_count": sum(v2_pass),
            "posthoc_gate_policy_v2_full_pass_rate": sum(v2_pass) / len(rows),
            "posthoc_failed_only_on_strict_2022": len(only_2022),
            "posthoc_worst_2022_among_failures": min(
                (row["centre"]["full"]["yearly"].get("2022", 0.0) for row in failed_rows),
                default=None,
            ),
        },
        "plateau": {
            "minimum_full_pass_rate": plateau["minimum_full_pass_rate"],
            "minimum_sealed_pass_rate": plateau["minimum_sealed_pass_rate"],
            "minimum_sealed_advantage_rate": plateau["minimum_sealed_advantage_rate"],
            "parameter_robust": robust,
        },
        "rows": rows,
        "decision": {
            "hold30_parameter_robust": robust,
            "no_leverage": True,
            "paper_target_generated": False,
            "orders_generated": False,
            "production_enabled": False,
        },
        "known_limits": prereg["known_limits"],
    }


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# R379 · hold-30 组合参数蒙特卡洛",
        "",
        f"中心 `{report['centre']}` 与 20 日对照 `{report['control']}` 使用同一组 {report['samples']} 个五维拉丁超立方样本。",
        "",
        "| 指标 | 中心 | 对照 |",
        "| --- | ---: | ---: |",
        f"| 固定基准封存收益 | {report['baseline']['centre']['total_return']:.2%} | {report['baseline']['control']['total_return']:.2%} |",
        f"| 封存收益 P05/中位/P95 | {summary['centre_sealed_return_p05_median_p95'][0]:.2%} / {summary['centre_sealed_return_p05_median_p95'][1]:.2%} / {summary['centre_sealed_return_p05_median_p95'][2]:.2%} | {summary['control_sealed_return_p05_median_p95'][0]:.2%} / {summary['control_sealed_return_p05_median_p95'][1]:.2%} / {summary['control_sealed_return_p05_median_p95'][2]:.2%} |",
        "",
        f"- 全窗口 v2 点门槛通过 {summary['full_pass_count']}/{report['samples']}（{summary['full_pass_rate']:.1%}）。",
        f"- 封存窗口门槛通过 {summary['sealed_pass_count']}/{report['samples']}（{summary['sealed_pass_rate']:.1%}）。",
        f"- 封存段中心跑赢对照 {summary['sealed_advantage_count']}/{report['samples']}（{summary['sealed_advantage_rate']:.1%}）。",
        f"- 封存超额 P05/中位/P95：{summary['advantage_p05_median_p95'][0]:+.2%} / {summary['advantage_p05_median_p95'][1]:+.2%} / {summary['advantage_p05_median_p95'][2]:+.2%}；最差 {summary['advantage_worst']:+.2%}，最好 {summary['advantage_best']:+.2%}。",
        "",
        f"**参数平台：{'通过' if report['plateau']['parameter_robust'] else '未通过'}。**",
        "",
        f"- 事后诊断（不改预注册门槛）：按 gate policy v2 的 2022 >= -5% 口径，全窗口通过 {summary['posthoc_gate_policy_v2_full_pass_count']}/{report['samples']}（{summary['posthoc_gate_policy_v2_full_pass_rate']:.1%}）；未通过的 {report['samples'] - summary['full_pass_count']} 个样本里有 {summary['posthoc_failed_only_on_strict_2022']} 个只是 2022 年略低于 0，最差 {summary['posthoc_worst_2022_among_failures']:+.2%}。",
        "- 只扰动组合层参数，B 袖、A/C 袖和选股逻辑固定。",
        "- 拉丁超立方样本与 R370 的审计相关，不是独立试验。",
        "- 无杠杆、不生成纸面目标、订单或生产配置。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    for path in (OUTPUT, OUTPUT.with_name(OUTPUT.name + ".sha256"), RESULT, RESULT.with_name(RESULT.name + ".sha256")):
        if path.exists():
            raise RuntimeError(f"R379 output exists: {path}")
    report = build_report()
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha256(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    RESULT.write_text(markdown(report), encoding="utf-8")
    RESULT.with_name(RESULT.name + ".sha256").write_text(f"{sha256(RESULT)}  {RESULT.name}\n", encoding="ascii")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(json.dumps(report["plateau"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
